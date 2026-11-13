"""Task and Response (spec/01-model.md)."""
import codecs, json, re
from dataclasses import dataclass, field, fields, replace
from .urls import join

CHARSET = re.compile(rb"charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)
META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)

# WHATWG Encoding Standard labels -> the Python codec that decodes like browsers do. Python's own
# shift_jis/euc_kr/gb2312/latin-1 are stricter than what pages labeled that way contain, and labels
# outside this list (utf-7, x-user-defined, ...) fall back to UTF-8, as in the Rust port.
WHATWG = {label: codec for codec, labels in {
    "utf-8": "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8",
    "cp866": "866 cp866 csibm866 ibm866",
    "iso8859-2": "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 iso_8859-2:1987 l2 latin2",
    "iso8859-3": "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 iso_8859-3:1988 l3 latin3",
    "iso8859-4": "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 iso_8859-4:1988 l4 latin4",
    "iso8859-5": "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 iso_8859-5 iso_8859-5:1988",
    "iso8859-6": "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 iso-8859-6 iso-8859-6-e "
                 "iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 iso_8859-6:1987",
    "iso8859-7": "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 iso8859-7 iso88597 "
                 "iso_8859-7 iso_8859-7:1987 sun_eu_greek",
    "iso8859-8": "csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 iso8859-8 iso88598 "
                 "iso_8859-8 iso_8859-8:1988 visual csiso88598i iso-8859-8-i logical",
    "iso8859-10": "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6",
    "iso8859-13": "iso-8859-13 iso8859-13 iso885913",
    "iso8859-14": "iso-8859-14 iso8859-14 iso885914",
    "iso8859-15": "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9",
    "iso8859-16": "iso-8859-16",
    "koi8-r": "cskoi8r koi koi8 koi8-r koi8_r",
    "koi8-u": "koi8-ru koi8-u",
    "mac-roman": "csmacintosh mac macintosh x-mac-roman",
    "mac-cyrillic": "x-mac-cyrillic x-mac-ukrainian",
    "cp874": "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874",
    "cp1250": "cp1250 windows-1250 x-cp1250",
    "cp1251": "cp1251 windows-1251 x-cp1251",
    "cp1252": "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 iso8859-1 iso88591 "
              "iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 x-cp1252",
    "cp1253": "cp1253 windows-1253 x-cp1253",
    "cp1254": "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 iso_8859-9:1989 l5 latin5 "
              "windows-1254 x-cp1254",
    "cp1255": "cp1255 windows-1255 x-cp1255",
    "cp1256": "cp1256 windows-1256 x-cp1256",
    "cp1257": "cp1257 windows-1257 x-cp1257",
    "cp1258": "cp1258 windows-1258 x-cp1258",
    "gb18030": "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk gb18030",
    "big5hkscs": "big5 big5-hkscs cn-big5 csbig5 x-x-big5",
    "euc-jp": "cseucpkdfmtjapanese euc-jp x-euc-jp",
    "iso2022-jp": "csiso2022jp iso-2022-jp",
    "cp932": "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis",
    "cp949": "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 ksc5601 ksc_5601 "
             "windows-949",
    "utf-16-be": "unicodefffe utf-16be",
    "utf-16-le": "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le",
    "replacement": "csiso2022kr hz-gb-2312 iso-2022-cn iso-2022-cn-ext iso-2022-kr replacement",
}.items() for label in labels.split()}


@dataclass
class Task:
    url: str
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    body: str | None = None
    priority: int = 0
    depth: int = 0
    meta: dict = field(default_factory=dict)
    dedup: bool = True

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Response:
    task: Task
    url: str
    status: int | None
    outcome: str
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    attempts: int = 0
    elapsed_ms: int = 0
    error: str | None = None

    def text(self):
        """Decode by BOM, then content-type charset, then <meta> charset, then UTF-8 (spec/01-model.md)."""
        b = self.body
        for bom, enc in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            if b.startswith(bom):
                return b[len(bom):].decode(enc, "replace")
        m = CHARSET.search(self.headers.get("content-type", "").encode()) or META_CHARSET.search(b[:1024])
        codec = WHATWG.get(m.group(1).decode().lower() if m else "", "utf-8")  # unknown label -> UTF-8
        if codec == "replacement":  # labels browsers refuse to decode (XSS risk): one U+FFFD
            return "\ufffd" if b else ""
        return b.decode(codec, "replace")

    def json(self):
        return json.loads(self.body)

    def follow(self, href, **task_fields):
        """New Task for a link on this page, one level deeper."""
        return replace(Task(join(self.url, href)), depth=self.task.depth + 1, **task_fields)
