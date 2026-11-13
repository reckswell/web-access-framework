//! Local HTTP server for bench/run.py. It listens on every address, so each 127.x.y.z URL reaches it
//! as a separate host. Every request is answered after a fixed delay: /robots.txt with a small file on
//! the same connection, any other path with a page of the given size and `connection: close`.
//!
//! Each host is fetched once, so a run opens one connection per host. With `reset` (the default) the
//! server waits for the client to close and then resets the connection, so neither side keeps a
//! TIME_WAIT entry: on Windows, hundreds of thousands of those slow down every later connection and
//! the benchmark would measure that instead of the engine. `fin` closes normally, as real servers do.
//!
//! Usage: cargo run --release --example bench_server -- <port> <delay_ms> <page_kb> [reset|fin]
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpSocket, TcpStream};

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
    let reset = std::env::args().nth(4).is_none_or(|m| m != "fin");
    let socket = TcpSocket::new_v4()?;
    socket.set_reuseaddr(cfg!(unix))?; // rebind while old connections linger (on Windows the flag means more)
    socket.bind(([0, 0, 0, 0], port).into())?;
    let listener = socket.listen(4096)?; // Windows refuses connections outright once the backlog is full
    loop {
        match listener.accept().await {
            Ok((sock, _)) => drop(tokio::spawn(serve(sock, delay, robots, page, reset))),
            Err(_) => tokio::time::sleep(Duration::from_millis(5)).await, // out of sockets: let some close
        }
    }
}

async fn serve(mut sock: TcpStream, delay: Duration, robots: &[u8], page: &[u8], reset: bool) -> std::io::Result<()> {
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
            sock.write_all(page).await?;
            if reset { // the client closes once it has read the page; then reset instead of closing
                let _ = tokio::time::timeout(Duration::from_secs(10), sock.read(&mut chunk)).await;
                sock.set_zero_linger()?;
            }
            return Ok(());
        }
        sock.write_all(robots).await?;
    }
}
