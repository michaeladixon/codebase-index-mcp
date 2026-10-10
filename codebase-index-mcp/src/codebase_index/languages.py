"""Language registry and tree-sitter based symbol/reference extraction.

A config-driven AST walker (rather than per-grammar query files) keeps this
robust across grammar versions: dedicated node-type/field handling exists for
Python, JS/TS/TSX, C#, Java, Go, Rust, C, C++, Ruby, PHP, Kotlin and Swift,
and every other grammar in the language pack falls through to the same
generic tables, which still yield function/class-level extraction.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- extensions

EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".cs": "csharp",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".lua": "lua",
    ".sh": "bash", ".bash": "bash",
    ".pl": "perl", ".pm": "perl",
    ".r": "r",
    ".jl": "julia",
    ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang", ".hrl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml", ".mli": "ocaml",
    ".dart": "dart",
    ".zig": "zig",
    ".sql": "sql",
    ".html": "html", ".css": "css",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    # .razor: no grammar available -> indexed content-only (see below)
}

# Files with no tree-sitter grammar still get full-text indexed (grep parity).
CONTENT_ONLY_EXTS = {".md", ".txt", ".rst", ".ini", ".cfg", ".razor", ".cshtml"}

_ALT_NAMES = {
    "csharp": ("csharp", "c_sharp"),
    "tsx": ("tsx", "typescript"),
}

PROVIDER: str | None = None  # which grammar package actually supplied parsers
_PARSERS: dict = {}


def get_parser(lang: str):
    """Return a cached tree-sitter Parser for lang, or None."""
    global PROVIDER
    if lang in _PARSERS:
        return _PARSERS[lang]
    parser = None
    for mod, label in (("tree_sitter_language_pack", "tree-sitter-language-pack"),
                       ("tree_sitter_languages", "tree-sitter-languages")):
        try:
            gp = __import__(mod, fromlist=["get_parser"]).get_parser
        except Exception:
            continue
        for name in _ALT_NAMES.get(lang, (lang,)):
            try:
                parser = gp(name)
                break
            except Exception:
                continue
        if parser is not None:
            if PROVIDER is None:
                PROVIDER = label
            break
    _PARSERS[lang] = parser
    return parser


# ------------------------------------------------------------- node tables

DEF_KINDS = {
    "function_definition": "function",       # python, c, cpp, php
    "function_declaration": "function",      # js, go, kotlin, swift
    "function_item": "function",             # rust
    "function_signature": "function",        # ts declare
    "method_definition": "method",           # js/ts
    "method_declaration": "method",          # java, csharp, go
    "constructor_declaration": "method",     # java, csharp
    "init_declaration": "method",            # swift
    "singleton_method": "method",            # ruby
    "method": "method",                      # ruby
    "class_definition": "class",             # python
    "class_declaration": "class",            # js/ts/java/csharp/kotlin/php/swift
    "class_specifier": "class",              # cpp
    "class": "class",                        # ruby
    "record_declaration": "class",           # java/csharp records
    "object_declaration": "class",           # kotlin object
    "struct_specifier": "struct",            # c/cpp
    "struct_item": "struct",                 # rust
    "struct_declaration": "struct",          # csharp
    "interface_declaration": "interface",    # ts/java/csharp/php
    "protocol_declaration": "interface",     # swift
    "trait_item": "interface",               # rust trait
    "trait_declaration": "interface",        # php trait
    "enum_declaration": "enum",              # ts/java/csharp/php
    "enum_item": "enum",                     # rust
    "enum_specifier": "enum",                # c/cpp
    "type_alias_declaration": "type",        # ts
    "type_item": "type",                     # rust
    "type_definition": "type",               # c/cpp typedef
    "delegate_declaration": "type",          # csharp
    "impl_item": "impl",                     # rust
    "mod_item": "module",                    # rust
    "module": "module",                      # ruby / ts `module X {}`
    "internal_module": "namespace",          # ts namespace
    "namespace_declaration": "namespace",    # csharp
    "file_scoped_namespace_declaration": "namespace",  # csharp
    "namespace_definition": "namespace",     # cpp, php
    "const_item": "constant",                # rust
    "static_item": "constant",               # rust
}

LANG_EXTRA = {
    "csharp": {"property_declaration": "property"},
}

CONTAINER_KINDS = {"class", "struct", "interface", "enum", "impl",
                   "namespace", "module", "trait"}

IDENT_TYPES = {"identifier", "type_identifier", "field_identifier",
               "property_identifier", "simple_identifier", "constant",
               "name", "word", "constant_identifier"}

BODY_TYPES = {"block", "statement_block", "class_body", "declaration_list",
              "field_declaration_list", "enum_body", "interface_body",
              "compound_statement", "function_body", "object_body", "body",
              "impl_body", "trait_body", "namespace_body", "enum_class_body"}

CALL_TYPES = {"call", "call_expression", "method_invocation",
              "invocation_expression", "new_expression",
              "object_creation_expression", "macro_invocation",
              "function_call_expression", "scoped_call_expression",
              "member_call_expression"}

CALLEE_FIELDS = ("function", "name", "method", "constructor", "type")

IMPORT_TYPES = {"import_statement", "import_from_statement",
                "import_declaration", "using_directive", "use_declaration",
                "preproc_include", "import_header",
                "namespace_use_declaration"}

COMMENT_TYPES = {"comment", "line_comment", "block_comment", "doc_comment"}

FUNC_VALUE_TYPES = {"arrow_function", "function_expression", "function",
                    "generator_function"}

JS_LANGS = {"javascript", "typescript", "tsx"}


# --------------------------------------------------------------- utilities

def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _ident_text(node, src: bytes, depth: int = 0):
    """Drill into a node to find its defining/rightmost identifier."""
    if node is None:
        return None
    if node.type in IDENT_TYPES:
        return _text(node, src)
    if depth >= 5:
        return None
    for f in ("name", "declarator", "field", "attribute", "property"):
        ch = node.child_by_field_name(f)
        if ch is not None:
            t = _ident_text(ch, src, depth + 1)
            if t:
                return t
    for ch in node.named_children:
        t = _ident_text(ch, src, depth + 1)
        if t:
            return t
    return None


def _def_name(node, src: bytes, lang: str, kind: str):
    if kind == "impl":  # rust: name the impl after its type
        t = node.child_by_field_name("type")
        return _ident_text(t, src) if t is not None else None
    n = node.child_by_field_name("name")
    if n is not None:
        return _ident_text(n, src) or _text(n, src)[:60]
    if lang in ("c", "cpp"):
        d = node.child_by_field_name("declarator")
        if d is not None:
            return _ident_text(d, src)
    for ch in node.named_children[:6]:
        if ch.type in IDENT_TYPES:
            return _text(ch, src)
    return None


def _signature(node, src: bytes) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        for ch in node.children:
            if ch.type in BODY_TYPES:
                body = ch
                break
    if body is not None and body.start_byte > node.start_byte:
        txt = src[node.start_byte:body.start_byte].decode("utf-8", "replace")
    else:
        txt = _text(node, src).split("\n", 1)[0]
    txt = re.sub(r"\s+", " ", txt).strip()
    txt = txt.rstrip("{:").rstrip()
    return txt[:160]


def _clean_comment(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"^\s*(///|//!|//|#|/\*+|\*+/?|--)\s?", "", ln).strip()
        ln = ln.rstrip("*/").strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines[:3])


def _doc(node, src: bytes, lang: str) -> str:
    # Python docstring: first string statement of body.
    if lang == "python":
        body = node.child_by_field_name("body")
        if body is not None and body.named_children:
            first = body.named_children[0]
            if first.type == "expression_statement" and first.named_children:
                s = first.named_children[0]
                if s.type == "string":
                    raw = _text(s, src).strip()
                    raw = re.sub(r"^[rbuRBU]*('''|\"\"\"|'|\")", "", raw)
                    raw = re.sub(r"('''|\"\"\"|'|\")$", "", raw)
                    lines = [l.strip() for l in raw.splitlines() if l.strip()]
                    return "\n".join(lines[:3])
    # Otherwise: contiguous leading comments directly above the definition.
    anchor = node
    if node.parent is not None and node.parent.type == "decorated_definition":
        anchor = node.parent
    parts = []
    expected = anchor.start_point[0] - 1
    prev = anchor.prev_named_sibling
    while prev is not None and prev.type in COMMENT_TYPES and len(parts) < 3:
        if prev.end_point[0] != expected:
            break
        parts.append(_text(prev, src))
        expected = prev.start_point[0] - 1
        prev = prev.prev_named_sibling
    if not parts:
        return ""
    joined = "\n".join(reversed(parts))
    cleaned = _clean_comment(joined)
    return "\n".join(cleaned.splitlines()[:3])


def _callee_name(node, src: bytes):
    c = None
    for f in CALLEE_FIELDS:
        c = node.child_by_field_name(f)
        if c is not None:
            break
    if c is None:
        c = node.named_children[0] if node.named_children else None
    if c is None:
        return None
    if c.type in IDENT_TYPES:
        return _text(c, src)
    return _ident_text(c, src)


def _strip_quotes(s: str) -> str:
    return s.strip().strip("'\"<>")


def _descendants_of_type(node, type_name: str, out=None):
    if out is None:
        out = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == type_name:
            out.append(n)
        stack.extend(n.children)
    return out


def _handle_import(node, lang: str, src: bytes, imports: list, refs: set):
    line = node.start_point[0] + 1
    t = node.type
    if t == "import_declaration" and lang == "go":
        specs = _descendants_of_type(node, "import_spec")
        for sp in specs:
            imports.append((sp.start_point[0] + 1,
                            _strip_quotes(_text(sp, src).split("\n")[0])[:120]))
        if specs:
            return
    if t == "import_from_statement":  # python
        mod = node.child_by_field_name("module_name")
        m = _text(mod, src) if mod is not None else ""
        names = []
        for ch in node.children_by_field_name("name"):
            nm = ch.child_by_field_name("name") if ch.type == "aliased_import" else ch
            if nm is None:
                continue
            leaf = _text(nm, src).split(".")[-1]
            names.append(leaf)
            refs.add((line, leaf, "import"))
        entry = m + (" -> " + ",".join(names) if names else "")
        imports.append((line, entry[:120]))
        return
    if t == "import_statement" and lang == "python":
        for ch in node.children_by_field_name("name"):
            nm = ch.child_by_field_name("name") if ch.type == "aliased_import" else ch
            if nm is not None:
                imports.append((line, _text(nm, src)[:120]))
        return
    if t == "import_statement":  # js/ts
        srcn = node.child_by_field_name("source")
        mod = _strip_quotes(_text(srcn, src)) if srcn is not None else ""
        names = []
        for sp in _descendants_of_type(node, "import_specifier"):
            nm = sp.child_by_field_name("name")
            if nm is not None:
                leaf = _text(nm, src)
                names.append(leaf)
                refs.add((line, leaf, "import"))
        imports.append((line, (mod + (" -> " + ",".join(names) if names else ""))[:120]))
        return
    if t == "preproc_include":
        p = node.child_by_field_name("path")
        imports.append((line, _strip_quotes(_text(p, src)) if p is not None else ""))
        return
    txt = _text(node, src).split("\n")[0]
    txt = re.sub(r"^\s*(import|using|use|include|require)\b\s*", "", txt)
    imports.append((line, txt.rstrip(";").strip()[:120]))


# ----------------------------------------------------------------- extract

def extract(lang: str, root_node, src: bytes):
    """Walk the AST. Returns (symbols, refs, imports).

    symbols: list of dicts with parent as index into the same list.
    refs: set of (line, name, kind). imports: list of (line, module).
    """
    symbols: list[dict] = []
    refs: set = set()
    imports: list = []
    extra = LANG_EXTRA.get(lang, {})

    stack = [(root_node, None)]
    while stack:
        node, parent = stack.pop()
        t = node.type
        cur = parent
        kind = DEF_KINDS.get(t) or extra.get(t)

        if t == "type_spec" and lang == "go":
            ty = node.child_by_field_name("type")
            kind = {"struct_type": "struct",
                    "interface_type": "interface"}.get(
                        ty.type if ty is not None else "", "type")

        if kind:
            name = _def_name(node, src, lang, kind)
            if name:
                if kind == "function" and parent is not None and \
                        symbols[parent]["kind"] in CONTAINER_KINDS:
                    kind = "method"
                anchor = node
                if node.parent is not None and \
                        node.parent.type == "decorated_definition":
                    anchor = node.parent
                qual = (symbols[parent]["qualname"] + "." + name) \
                    if parent is not None else name
                if lang == "go" and t == "method_declaration":
                    # func (s *Server) Run() -> Server.Run
                    recv = node.child_by_field_name("receiver")
                    rts = _descendants_of_type(recv, "type_identifier") \
                        if recv is not None else []
                    if rts:
                        rt = min(rts, key=lambda n: n.start_byte)
                        qual = _text(rt, src) + "." + name
                symbols.append({
                    "name": name, "qualname": qual, "kind": kind,
                    "start": anchor.start_point[0] + 1,
                    "end": node.end_point[0] + 1,
                    "signature": _signature(node, src),
                    "doc": _doc(node, src, lang),
                    "parent": parent,
                })
                cur = len(symbols) - 1
        elif t == "variable_declarator" and lang in JS_LANGS:
            nm = node.child_by_field_name("name")
            val = node.child_by_field_name("value")
            if nm is not None and val is not None and val.type in FUNC_VALUE_TYPES:
                name = _text(nm, src)
                k = "method" if parent is not None and \
                    symbols[parent]["kind"] in CONTAINER_KINDS else "function"
                body = val.child_by_field_name("body")
                sig_end = body.start_byte if body is not None else node.end_byte
                sig = re.sub(r"\s+", " ", src[node.start_byte:sig_end]
                             .decode("utf-8", "replace")).strip()[:160]
                qual = (symbols[parent]["qualname"] + "." + name) \
                    if parent is not None else name
                symbols.append({
                    "name": name, "qualname": qual, "kind": k,
                    "start": node.start_point[0] + 1,
                    "end": node.end_point[0] + 1,
                    "signature": sig, "doc": _doc(node, src, lang),
                    "parent": parent,
                })
                cur = len(symbols) - 1
            elif nm is not None and parent is None and nm.type == "identifier":
                name = _text(nm, src)
                pnode = node.parent
                is_const = pnode is not None and \
                    pnode.type == "lexical_declaration" and \
                    pnode.children and _text(pnode.children[0], src) == "const"
                if is_const and name.isupper() and len(name) > 1:
                    symbols.append({
                        "name": name, "qualname": name, "kind": "constant",
                        "start": node.start_point[0] + 1,
                        "end": node.end_point[0] + 1,
                        "signature": re.sub(r"\s+", " ", _text(node, src)
                                            .split("\n")[0]).strip()[:160],
                        "doc": "", "parent": None,
                    })
        elif t == "assignment" and lang == "python" and parent is None:
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                name = _text(left, src)
                if name.isupper() and len(name) > 1:
                    symbols.append({
                        "name": name, "qualname": name, "kind": "constant",
                        "start": node.start_point[0] + 1,
                        "end": node.end_point[0] + 1,
                        "signature": _text(node, src).split("\n")[0][:160],
                        "doc": "", "parent": None,
                    })
        elif t == "const_spec" and lang == "go":
            if parent is None or symbols[parent]["kind"] in CONTAINER_KINDS:
                for nm in node.children_by_field_name("name"):
                    symbols.append({
                        "name": _text(nm, src), "qualname": _text(nm, src),
                        "kind": "constant",
                        "start": node.start_point[0] + 1,
                        "end": node.end_point[0] + 1,
                        "signature": _text(node, src).split("\n")[0][:160],
                        "doc": "", "parent": None,
                    })

        if t in CALL_TYPES:
            cn = _callee_name(node, src)
            if cn and len(cn) < 80:
                refs.add((node.start_point[0] + 1, cn, "call"))
        elif t == "decorator":
            cn = _ident_text(node, src)
            if cn:
                refs.add((node.start_point[0] + 1, cn, "call"))
        elif t == "type_identifier":
            refs.add((node.start_point[0] + 1, _text(node, src), "type"))
        elif t in IMPORT_TYPES:
            try:
                _handle_import(node, lang, src, imports, refs)
            except Exception:
                pass

        for ch in reversed(node.children):
            stack.append((ch, cur))

    return symbols, refs, imports
