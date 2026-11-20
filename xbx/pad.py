"""Padding between functions: what a linker or an assembler's `align` leaves there."""

# int3 and nop (the compilers' and linker's fill), then MASM's multi-byte fills for `align`
FILLS = (b'\xcc', b'\x90',
         b'\x8b\xff',                          # mov edi, edi
         b'\x8b\xc0',                          # mov eax, eax
         b'\x8d\x49\x00',                      # lea ecx, [ecx+0]
         b'\x8d\x64\x24\x00',                  # lea esp, [esp+0]
         b'\x8d\x9b\x00\x00\x00\x00',          # lea ebx, [ebx+0]
         b'\x8d\xa4\x24\x00\x00\x00\x00')      # lea esp, [esp+0]
MAX_GAP = 15


def is_padding(data):
    """True when data is nothing but fill sequences (an empty string is padding)."""
    data = bytes(data)
    k = 0
    while k < len(data):
        for fill in FILLS:
            if data.startswith(fill, k):
                k += len(fill)
                break
        else:
            return False
    return True
