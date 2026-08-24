"""Safe boolean retrieval queries for the disposable document search index."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


FIELDS = {
    "tag": "tags", "tags": "tags",
    "name": "path", "datei": "path", "path": "path", "pfad": "path",
    "text": "content", "inhalt": "content",
    "note": "notes", "notiz": "notes", "notes": "notes",
    "state": "state", "status": "state",
    "attr": "attributes", "attribut": "attributes", "attributes": "attributes",
}
TOKEN = re.compile(r'''\s*(?:(?P<lpar>\()|(?P<rpar>\))|(?P<tilde>~)|(?P<quoted>"(?:[^"\\]|\\.)*")|(?P<word>[^\s()~]+))''')


@dataclass(frozen=True)
class CompiledQuery:
    fts: str
    where: str
    parameters: tuple[str, ...]
    requires_sql: bool = False


class SearchQuery:
    def __init__(self, source: str):
        self.tokens = self._tokens(source)
        self.position = 0
        self.parameters: list[str] = []
        self.requires_sql = False

    def compile(self) -> CompiledQuery:
        if not self.tokens:
            raise ValueError("Bitte mindestens einen Suchbegriff eingeben.")
        fts, sql = self._or_expression()
        if self.position != len(self.tokens):
            raise ValueError(f"Unerwarteter Ausdruck: {self.tokens[self.position][1]}")
        return CompiledQuery(fts, sql, tuple(self.parameters), self.requires_sql)

    @staticmethod
    def _tokens(source: str) -> list[tuple[str, str]]:
        if len(source) > 2_000:
            raise ValueError("Die Suchanfrage ist auf 2.000 Zeichen begrenzt.")
        result, end = [], 0
        for match in TOKEN.finditer(source):
            if source[end:match.start()].strip():
                raise ValueError("Die Suchanfrage enthält ein ungültiges Zeichen.")
            kind = next(name for name, value in match.groupdict().items() if value is not None)
            value = match.group(kind)
            upper = value.upper()
            if kind == "word" and upper in {"AND", "UND"}: kind, value = "and", "AND"
            elif kind == "word" and upper in {"OR", "ODER"}: kind, value = "or", "OR"
            elif kind == "word" and upper in {"NOT", "NICHT"}: kind, value = "not", "NOT"
            elif kind == "word" and upper == "XOR": kind, value = "xor", "XOR"
            elif kind == "word" and upper == "NOR": kind, value = "nor", "NOR"
            elif kind == "word" and upper == "CONTAINS": kind, value = "tilde", "~"
            result.append((kind, value)); end = match.end()
        if source[end:].strip():
            raise ValueError("Die Suchanfrage ist unvollständig.")
        if len(result) > 100:
            raise ValueError("Die Suchanfrage enthält zu viele Teile.")
        return result

    def _or_expression(self) -> tuple[str, str]:
        fts, sql = self._xor_expression()
        while self.position < len(self.tokens) and self.tokens[self.position][0] in {"or", "nor"}:
            operator = self.tokens[self.position][0]; self.position += 1
            right_fts, right_sql = self._xor_expression()
            if operator == "or":
                fts, sql = f"({fts} OR {right_fts})", f"({sql} OR {right_sql})"
            else:
                self.requires_sql = True
                fts, sql = f"NOT ({fts} OR {right_fts})", f"NOT ({sql} OR {right_sql})"
        return fts, sql

    def _xor_expression(self) -> tuple[str, str]:
        fts, sql = self._and_expression()
        while self._accept("xor"):
            right_fts, right_sql = self._and_expression()
            self.requires_sql = True
            fts = f"({fts} XOR {right_fts})"
            sql = f"((CASE WHEN {sql} THEN 1 ELSE 0 END) + (CASE WHEN {right_sql} THEN 1 ELSE 0 END) = 1)"
        return fts, sql

    def _and_expression(self) -> tuple[str, str]:
        fts, sql = self._factor()
        while self.position < len(self.tokens) and self.tokens[self.position][0] not in {"or", "xor", "nor", "rpar"}:
            self._accept("and")  # adjacent terms imply AND
            right_fts, right_sql = self._factor()
            fts, sql = f"({fts} AND {right_fts})", f"({sql} AND {right_sql})"
        return fts, sql

    def _factor(self) -> tuple[str, str]:
        if self._accept("not"):
            self.requires_sql = True
            fts, sql = self._factor()
            return f"NOT ({fts})", f"NOT ({sql})"
        if self._accept("lpar"):
            fts, sql = self._or_expression()
            if not self._accept("rpar"):
                raise ValueError("Eine schließende Klammer fehlt.")
            return f"({fts})", f"({sql})"
        if self.position >= len(self.tokens) or self.tokens[self.position][0] in {"and", "or", "xor", "nor", "not", "rpar"}:
            raise ValueError("Zwischen den Verknüpfungen fehlt ein Suchbegriff.")
        kind, raw = self.tokens[self.position]; self.position += 1
        field = ""
        value = raw
        contains = kind == "tilde"
        if contains:
            if self.position >= len(self.tokens) or self.tokens[self.position][0] not in {"word", "quoted"}:
                raise ValueError("Nach ~ fehlt ein Suchbegriff.")
            kind, value = self.tokens[self.position]; self.position += 1
        elif kind == "word" and self._accept("tilde"):
            field = FIELDS.get(raw.casefold(), "")
            if not field:
                raise ValueError(f"Unbekanntes Suchfeld: {raw}")
            if self.position >= len(self.tokens) or self.tokens[self.position][0] not in {"word", "quoted"}:
                raise ValueError("Nach ~ fehlt ein Suchbegriff.")
            kind, value = self.tokens[self.position]; self.position += 1
            contains = True
        if kind == "word" and ":" in raw:
            alias, value = raw.split(":", 1)
            field = FIELDS.get(alias.casefold(), "")
            if not field:
                raise ValueError(f"Unbekanntes Suchfeld: {alias}")
            if not value:
                if self.position >= len(self.tokens) or self.tokens[self.position][0] not in {"word", "quoted"}:
                    raise ValueError(f"Nach {alias}: fehlt ein Suchbegriff.")
                _, value = self.tokens[self.position]; self.position += 1
        phrase = self._value(value)
        prefix = phrase.endswith("*")
        if prefix: phrase = phrase[:-1]
        if not phrase or phrase == "*":
            raise ValueError("Ein alleinstehender Platzhalter ist nicht erlaubt.")
        if contains:
            self.requires_sql = True
        escaped = phrase.replace('"', '""')
        fts_term = f'"{escaped}"' + ("*" if prefix else "")
        if field:
            fts_term = f"{field} : {fts_term}"
            sql_columns = [field]
        else:
            sql_columns = ["path", "state", "tags", "notes", "attributes", "content"]
        pattern = phrase.replace("%", "\\%").replace("_", "\\_") + ("%" if prefix else "")
        clauses = []
        for column in sql_columns:
            clauses.append(f"{column} LIKE ? ESCAPE '\\'")
            self.parameters.append(f"%{pattern}%" if not prefix else f"%{pattern}")
        return fts_term, "(" + " OR ".join(clauses) + ")"

    @staticmethod
    def _value(raw: str) -> str:
        if raw.startswith('"'):
            try:
                return str(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise ValueError("Ungültige Zeichenfolge in Anführungszeichen.") from exc
        return raw

    def _accept(self, kind: str) -> bool:
        if self.position < len(self.tokens) and self.tokens[self.position][0] == kind:
            self.position += 1
            return True
        return False


def compile_query(source: str) -> CompiledQuery:
    return SearchQuery(source.strip()).compile()
