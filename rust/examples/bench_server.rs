//! Local HTTP server for bench/run.py. It listens on every address, so each 127.x.y.z URL reaches it
//! as a separate host. Every request is answered after a fixed delay: /robots.txt with a small file on
//! the same connection, any other path with a page of the given size, after which the connection
//! closes (each host is fetched once, and the server keeps the TIME_WAIT sockets, not the client).
//!
//! Usage: cargo run --release --example bench_server -- <port> <delay_ms> <page_kb>
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

fn response(body: &str, close: bool) -> &'static [u8] {
    let conn = if close { "close" } else { "keep-alive" };
    let r = format!("HTTP/1.1 200 OK\r\ncontent-type: text/html\r\ncontent-length: {}\r\nconnection: {conn}\r\n\r\n{body}",
                    body.len());
    Box::leak(r.into_bytes().into_boxed_slice()) // built once, shared by every connection
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let arg = |i: usize| std::env::args().nth(i).and_then(|a| a.parse::<u64>().ok())
        .expect("usage: bench_server <port> <delay_ms> <page_kb>");
    let (port, delay, kb) = (arg(1) as u16, Duration::from_millis(arg(2)), arg(3) as usize);
    let robots = response("User-agent: *\nDisallow: /private\n", false);
    let page = response(&format!("<!doctype html><title>bench</title><p>{}</p>", "x".repeat(kb * 1024)), true);
    let listener = TcpListener::bind(("0.0.0.0", port)).await?;
    loop {
        match listener.accept().await {
            Ok((sock, _)) => drop(tokio::spawn(serve(sock, delay, robots, page))),
            Err(_) => tokio::time::sleep(Duration::from_millis(5)).await, // out of sockets: let some close
        }
    }
}

async fn serve(mut sock: TcpStream, delay: Duration, robots: &[u8], page: &[u8]) -> std::io::Result<()> {
    let mut buf = Vec::with_capacity(1024);
    let mut chunk = [0u8; 4096];
    loop {
        let end = loop { // read one request head
            if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break i + 4;
            }
            let n = sock.read(&mut chunk).await?;
            if n == 0 {
                return Ok(());
            }
            buf.extend_from_slice(&chunk[..n]);
        };
        let is_robots = buf.starts_with(b"GET /robots.txt ");
        buf.drain(..end);
        if !delay.is_zero() {
            tokio::time::sleep(delay).await;
        }
        if !is_robots {
            return sock.write_all(page).await;
        }
        sock.write_all(robots).await?;
    }
}
