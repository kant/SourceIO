from enum import IntEnum

from ...byte_io_mdl import ByteIO
from .dummy import Dummy
from .header_block import InfoBlock
from ..lz4 import uncompress
from .common import SourceVector, SourceVector4D, SourceVector2D


class KVFlag(IntEnum):
    Nothing = 0
    Resource = 1
    DeferredResource = 2


class KVType(IntEnum):
    STRING_MULTI = 0  # STRING_MULTI doesn't have an ID
    NULL = 1
    BOOLEAN = 2
    INT64 = 3
    UINT64 = 4
    DOUBLE = 5
    STRING = 6
    BINARY_BLOB = 7
    ARRAY = 8
    OBJECT = 9
    ARRAY_TYPED = 10
    INT32 = 11
    UINT32 = 12
    BOOLEAN_TRUE = 13
    BOOLEAN_FALSE = 14
    INT64_ZERO = 15
    INT64_ONE = 16
    DOUBLE_ZERO = 17
    DOUBLE_ONE = 18


class BinaryKeyValue(Dummy):
    ENCODING = (0x46, 0x1A, 0x79, 0x95, 0xBC, 0x95, 0x6C, 0x4F, 0xA7, 0x0B, 0x05, 0xBC, 0xA1, 0xB7, 0xDF, 0xD2)
    FORMAT = (0x7C, 0x16, 0x12, 0x74, 0xE9, 0x06, 0x98, 0x46, 0xAF, 0xF2, 0xE6, 0x3E, 0xB5, 0x90, 0x37, 0xE7)
    SIG = (0x56, 0x4B, 0x56, 0x03)
    SIG2 = (0x01, 0x33, 0x56, 0x4B)
    indent = 0

    def __init__(self, block_info: InfoBlock = None):
        super().__init__()
        self.mode = 0
        self.strings = []
        self.types = []
        self.current_type = 0
        self.info_block = block_info
        self.kv = []
        self.flags = 0
        self.buffer = ByteIO()  # type: ByteIO

        self.bin_blob_count = 0
        self.bin_blob_offset = -1
        self.int_count = 0
        self.int_offset = -1
        self.double_count = 0
        self.double_offset = -1

    def read(self, reader: ByteIO):
        fourcc = reader.read_bytes(4)
        assert tuple(fourcc) == self.SIG or tuple(fourcc) == self.SIG2, 'Invalid KV Signature'
        if tuple(fourcc) == self.SIG2:
            self.read_v2(reader)
        else:
            self.read_v1(reader)

    def read_v1(self, reader):
        encoding = reader.read_bytes(16)
        assert tuple(encoding) == self.ENCODING, 'Unrecognized KV3 Encoding'
        fmt = reader.read_bytes(16)
        assert tuple(fmt) == self.FORMAT, 'Unrecognised KV3 Format'
        self.flags = reader.read_bytes(4)
        if self.flags[3] & 0x80:
            self.buffer.write_bytes(
                reader.read_bytes(self.info_block.block_size - (reader.tell() - self.info_block.absolute_offset)))
        working = True
        while reader.tell() != reader.size() and working:
            block_mask = reader.read_uint16()
            for i in range(16):
                if block_mask & (1 << i) > 0:
                    offset_and_size = reader.read_uint16()
                    offset = ((offset_and_size & 0xFFF0) >> 4) + 1
                    size = (offset_and_size & 0x000F) + 3
                    lookup_size = offset if offset < size else size
                    entry = self.buffer.tell()
                    self.buffer.seek(entry - offset)
                    data = self.buffer.read_bytes(lookup_size)
                    self.buffer.seek(entry)
                    while size > 0:
                        self.buffer.write_bytes(data[:lookup_size if lookup_size < size else size])
                        size -= lookup_size
                else:
                    data = reader.read_int8()
                    self.buffer.write_int8(data)
                if self.buffer.size() == (self.flags[2] << 16) + (self.flags[1] << 8) + self.flags[0]:
                    working = False
                    break
        self.buffer.seek(0)
        string_count = self.buffer.read_uint32()
        for i in range(string_count):
            self.strings.append(self.buffer.read_ascii_string())
        self.parse(self.buffer, self.kv, True)
        self.buffer.close()
        del self.buffer
        self.empty = False

    def read_v2(self, reader: ByteIO):
        fmt = reader.read_bytes(16)
        assert tuple(fmt) == self.FORMAT, 'Unrecognised KV3 Format'

        compression_method = reader.read_uint32()
        self.bin_blob_count = reader.read_uint32()
        self.int_count = reader.read_uint32()
        self.double_count = reader.read_uint32()
        if compression_method == 0:
            length = reader.read_uint32()
            self.buffer.write_bytes(reader.read_bytes(length))
        elif compression_method == 1:
            uncompressed_size = reader.read_uint32()
            compressed_size = self.info_block.block_size - (reader.tell() - self.info_block.absolute_offset)
            data = reader.read_bytes(compressed_size)
            u_data = uncompress(data)
            # with open("TEST.BIN",'wb') as f:
            #     f.write(u_data)
            assert len(u_data) == uncompressed_size, "Decompressed data size does not match expected size"
            self.buffer.write_bytes(u_data)
        else:
            raise NotImplementedError("Unknown KV3 compression method")

        self.buffer.seek(self.bin_blob_count)
        if self.bin_blob_count:
            self.buffer.seek(self.buffer.tell() + (4 - (self.buffer.tell() % 4)))
        string_count = self.buffer.read_uint32()
        kv_data_offset = self.buffer.tell()
        self.int_offset = self.buffer.tell()
        self.buffer.seek(self.buffer.tell() + self.int_count * 4)
        self.double_offset = self.buffer.tell()
        self.buffer.seek(self.buffer.tell() + self.double_count * 8)
        for _ in range(string_count):
            self.strings.append(self.buffer.read_ascii_string())

        types_len = self.buffer.size() - self.buffer.tell() - 4
        for _ in range(types_len):
            self.types.append(self.buffer.read_uint8())

        self.buffer.seek(kv_data_offset)
        self.parse(self.buffer, self.kv, True)
        self.buffer.close()
        self.kv = {"PermModelData_t": self.kv[0]}
        del self.buffer

    def read_type(self, reader: ByteIO):
        if self.types:
            data_type = self.types[self.current_type]
            self.current_type += 1
        else:
            data_type = reader.read_int8()

        flag_info = KVFlag.Nothing
        if data_type & 0x80:
            data_type &= 0x7F
            if self.types:
                flag_info = KVFlag(self.types[self.current_type])
                self.current_type += 1
            else:
                flag_info = KVFlag(reader.read_int8())
        return KVType(data_type), flag_info

    def parse(self, reader: ByteIO, parent=None, in_array=False):
        name = None
        parent = parent

        if not in_array:
            string_id = reader.read_uint32()
            name = "ERROR" if string_id == -1 else self.strings[string_id]

        data_type, flag_info = self.read_type(reader)
        self.read_value(name, reader, data_type, flag_info, parent, in_array)
        

    def read_value(self, name, reader: ByteIO, data_type: KVType, flag: KVFlag, parent, is_array=False):
        add = lambda v: parent.update({name: v}) if not is_array else parent.append(v)
        if data_type == KVType.NULL:
            add(None)
            return
        elif data_type == KVType.BOOLEAN:
            with reader.save_current_pos():
                if self.bin_blob_offset > -1:
                    reader.seek(self.bin_blob_offset)
                    self.bin_blob_offset += 1
                add(reader.read_int8() > 0)
            if self.bin_blob_offset == -1:
                reader.skip(1)
            return

        elif data_type == KVType.INT64:
            with reader.save_current_pos():
                if self.double_offset > 0:
                    reader.seek(self.double_offset)
                    self.double_offset += 8
                add(reader.read_int64())
            if self.double_offset == 0:
                reader.skip(8)
            return

        elif data_type == KVType.UINT64:
            with reader.save_current_pos():
                if self.double_offset > -1:
                    reader.seek(self.double_offset)
                    self.double_offset += 8
                add(reader.read_uint64())
            if self.double_offset == -1:
                reader.skip(8)
            return

        elif data_type == KVType.DOUBLE:
            with reader.save_current_pos():
                if self.double_offset > -1:
                    reader.seek(self.double_offset)
                    self.double_offset += 8
                add(reader.read_double())
            if self.double_offset == -1:
                reader.skip(8)
            return

        elif data_type == KVType.DOUBLE_ZERO:
            add(0.0)
            return
        elif data_type == KVType.DOUBLE_ONE:
            add(1.0)
            return
        elif data_type == KVType.INT32:
            add(reader.read_int32())
            return
        elif data_type == KVType.STRING:
            string_id = reader.read_int32()
            if string_id == -1:
                add(None)
                return
            add(self.strings[string_id])
            return
        elif data_type == KVType.ARRAY:
            size = reader.read_uint32()
            arr = []
            
            for _ in range(size):
                self.parse(reader, arr, True)
            
            add(arr)
            return
        elif data_type == KVType.OBJECT:
            size = reader.read_uint32()
            tmp = {}
            
            for _ in range(size):
                self.parse(reader, tmp, False)
            
            add(tmp)
            if not parent:
                parent = tmp
        elif data_type == KVType.ARRAY_TYPED:
            t_array_size = reader.read_uint32()
            sub_type, sub_flag = self.read_type(reader)
            tmp = []
            
            for _ in range(t_array_size):
                self.read_value(name, reader, sub_type, sub_flag, tmp, True)

            
            if sub_type == KVType.DOUBLE and t_array_size == 3:
                tmp = SourceVector(*tmp)
            elif sub_type == KVType.DOUBLE and t_array_size == 4:
                tmp = SourceVector4D(*tmp)
            elif sub_type == KVType.DOUBLE and t_array_size == 2:
                tmp = SourceVector2D(*tmp)
            add(tmp)
        else:
            a = 1
        return parent
