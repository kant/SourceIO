import sys
from collections import OrderedDict
from enum import Enum
from typing import TextIO


def _is_end(ch: str):
    return ch in '\r\n'


def _is_identifier_start(ch: str):
    return ch and (ch.isalpha() or ch == '|')


def _is_identifier_part(ch: str):
    return ch and (ch.isalnum() or '|/_.*'.find(ch) >= 0)


class KVToken(Enum):
    STR = "string literal"
    NUM = "number literal"
    END = "end of input"
    PLUS = "'+'"
    OPEN = "'{'"
    CLOSE = "'}'"


class KVReader:
    def __init__(self, filename: str, file: TextIO):
        self.filename = filename
        self.file = file
        self._line = 1
        self._column = 1
        self._last = None
        self._last = self._read()

    def read(self):
        tok = self._last
        self._last = self._read()
        return tok

    def peek(self):
        return self._last

    def _read(self):
        while True:
            lc = self._line, self._column
            ch = self._next_char()

            if ch.isspace():
                continue

            if ch == '/' and self._peek_char() == '/':
                while not _is_end(self._next_char()):
                    pass
                continue

            if _is_identifier_start(ch):
                buf = ch
                while _is_identifier_part(self._peek_char()):
                    buf += self._next_char()
                return KVToken.STR, buf, lc

            if ch == '"':
                buf = ''
                while not _is_end(self._peek_char()) and self._peek_char() != '"':
                    buf += self._next_char()
                if self._next_char() != '"':
                    self._report('String literal is not closed', lc)
                return KVToken.STR, buf, lc

            if ch.isdigit():
                buf = ch
                while self._peek_char().isdigit():
                    buf += self._next_char()
                return KVToken.NUM, buf, lc

            if ch == '+':
                return KVToken.PLUS, None, lc

            if ch == '{':
                return KVToken.OPEN, None, lc

            if ch == '}':
                return KVToken.CLOSE, None, lc

            if ch == '\0':
                return KVToken.END, None, lc

            self._report(f'Unknown character \'{ch}\' ({ord(ch):02x})', lc)

    def _report(self, msg: str, pos: tuple):
        raise ValueError(f'{self.filename}:{pos[0]}:{pos[1]}: {msg}')

    def _next_char(self):
        ch = self.file.read(1)
        if 'b' in self.file.mode:
            ch = ch.decode('ascii')

        if ch == '':
            ch = '\0'

        if ch == '\r' and self._peek_char() == '\n':
            self.file.read(1)

        if ch == '\r' or ch == '\n':
            self._line += 1
            self._column = 1
            return '\n'

        self._column += 1
        return ch

    def _peek_char(self):
        pos = self.file.tell()
        ch = self.file.read(1)
        if 'b' in self.file.mode:
            ch = ch.decode('ascii')
        self.file.seek(pos)
        return ch


class KVParser(KVReader):
    def __init__(self, filename: str, file: TextIO):
        super().__init__(filename, file)

    def parse(self):
        pairs = []

        while self._match(KVToken.STR, required=False, consume=False):
            pairs.append(self.parse_pair())

        self._match(KVToken.END)

        return pairs if len(pairs) > 1 else pairs[0] if pairs else None

    def parse_pair(self):
        key = self._match(KVToken.STR)[1]

        if self._match(KVToken.PLUS, required=False, consume=False):
            key = [key]

            while self._match(KVToken.PLUS, required=False):
                key.append(self._match(KVToken.STR)[1])

        val = self.parse_value()
        return key, val

    def parse_value(self):
        tok, val, _ = self._match(KVToken.END, KVToken.STR, KVToken.NUM, KVToken.OPEN)

        if tok is KVToken.END:
            return None

        if tok is KVToken.STR:
            return val

        if tok is KVToken.NUM:
            return int(val)

        if tok is KVToken.OPEN:
            pairs = OrderedDict()

            while not self._match(KVToken.CLOSE, required=False):
                key, val = self.parse_pair()

                if isinstance(key, list):
                    for sub_key in key:
                        pairs.setdefault(sub_key, []).append(val)
                else:
                    pairs.setdefault(key, []).append(val)
            for key, val in pairs.items():
                if len(val) == 1:
                    pairs[key] = val[0]

            return pairs

    def _match(self, *types, required=True, consume=True):
        tok = self.peek()

        if tok[0] in types:
            return self.read() if consume else tok

        if required:
            buf = 'expected '

            for index, expected in enumerate(types):
                buf += expected.value

                if index == len(types) - 2:
                    buf += ' or '
                if index <= len(types) - 3:
                    buf += ', '

            buf += ' before '
            buf += tok[0].value

            self._report(buf, tok[2])

        return None


class KVWriter:
    def __init__(self, stream: TextIO):
        self.stream = stream

    def write(self, value, indentation: int, append_newline: bool):
        if isinstance(value, tuple):
            self.write_pair(value[0], value[1], indentation, indentation, append_newline)
        elif isinstance(value, dict):
            self.write_dict(value, indentation, append_newline)
        elif isinstance(value, list):
            self.write_list(value, indentation, append_newline)
        elif isinstance(value, str):
            self.write_string(value, indentation, append_newline)
        elif isinstance(value, int):
            self.write_number(value, indentation, append_newline)
        else:
            raise TypeError(f'Invalid type: {value.__class__}')

    def write_pair(self, key: str, value, indentation: int, key_indentation: int, append_newline: bool):
        if isinstance(value, dict):
            self.print(key, indentation, True)
            self.write(value, indentation, append_newline)
        else:
            if isinstance(value, list):
                for sub_value in value:
                    self.print(key, indentation, False)
                    self.write(sub_value, key_indentation, append_newline)
            else:
                self.print(key, indentation, False)
                self.write(value, key_indentation, append_newline)

    def write_dict(self, items: dict, indentation: int, append_newline: bool):
        key_max_indentation = max(map(len, items.keys())) // 4 + 1

        self.print('{', indentation, True)

        for key, value in items.items():
            key_indentation = key_max_indentation - len(key) // 4
            self.write_pair(key, value, indentation + 1, key_indentation, True)

        self.print('}', indentation, append_newline)

    def write_list(self, items: list, indentation: int, append_newline: bool):
        for index, item in enumerate(items):
            if index < len(items) - 1:
                self.write(item, indentation, True)
                self.print('', 0, True)
            else:
                self.write(item, indentation, append_newline)

    def write_string(self, value: str, indentation: int, append_newline: bool):
        self.print(value if value.isidentifier() else f'"{value}"', indentation, append_newline)

    def write_number(self, value: int, indentation: int, append_newline: bool):
        self.print(str(value), indentation, append_newline)

    def print(self, value, indent: int, append_newline: bool = True):
        self.stream.write('\t' * indent + value)

        if append_newline:
            self.stream.write('\n')


if __name__ == '__main__':
    data = KVParser('<input>', open('gameinfo.txt'))
    data = data.parse()

    KVWriter(sys.stdout).write(data, 0, True)
