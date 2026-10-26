"""Inspect tiled TIFF/BigTIFF layout without reading or decoding raster pixels.

This is a deliberately limited independent structural check, not certification
against every requirement of the COG specification. It checks IFD/table bounds,
tile ranges, IFDs before imagery, row-major tile order and overview-first data.
It supports the single-band, non-sparse COGs produced by this project.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct


_TYPE_BYTES = {
    1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2,
    9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8,
}
_INTEGER_TYPES = {1: "B", 3: "H", 4: "I", 8: "h", 9: "i", 13: "I",
                  16: "Q", 17: "q", 18: "Q"}
_NEEDED_TAGS = {254, 256, 257, 259, 277, 284, 317, 322, 323, 324, 325, 330}


def inspect_cog_layout(path):
    """Return JSON-compatible observations; raise ValueError on a failed check."""
    path = Path(path)
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        def read_at(offset, length):
            if offset < 0 or length < 0 or offset + length > file_size:
                raise ValueError(f"TIFF reference outside file: {offset}+{length}")
            stream.seek(offset)
            data = stream.read(length)
            if len(data) != length:
                raise ValueError("TIFF changed or became truncated while reading")
            return data

        byte_order = read_at(0, 2)
        if byte_order not in (b"II", b"MM"):
            raise ValueError("Not a TIFF byte-order marker")
        endian = "<" if byte_order == b"II" else ">"
        magic = struct.unpack(endian + "H", read_at(2, 2))[0]
        if magic == 43:
            if struct.unpack(endian + "HH", read_at(4, 4)) != (8, 0):
                raise ValueError("Unsupported BigTIFF offset size or reserved bytes")
            offset_bytes, count_bytes, entry_bytes, number_code = 8, 8, 20, "Q"
            first_ifd = struct.unpack(endian + "Q", read_at(8, 8))[0]
        elif magic == 42:
            offset_bytes, count_bytes, entry_bytes, number_code = 4, 2, 12, "I"
            first_ifd = struct.unpack(endian + "I", read_at(4, 4))[0]
        else:
            raise ValueError(f"Unsupported TIFF magic {magic}")
        if not first_ifd:
            raise ValueError("TIFF has no image directory")

        directories, seen, pending, metadata_ranges = [], set(), [first_ifd], []
        while pending:
            ifd_offset = pending.pop(0)
            if ifd_offset in seen:
                raise ValueError("Repeated or cyclic TIFF IFD reference")
            seen.add(ifd_offset)
            if len(seen) > 100:
                raise ValueError("Unexpectedly many TIFF directories")
            count_code = "Q" if magic == 43 else "H"
            n_entries = struct.unpack(endian + count_code,
                                      read_at(ifd_offset, count_bytes))[0]
            if n_entries > 4096:
                raise ValueError("Unexpectedly many TIFF directory entries")
            ifd_end = ifd_offset + count_bytes + n_entries * entry_bytes + offset_bytes
            metadata_ranges.append((ifd_offset, ifd_end))
            raw_entries = read_at(ifd_offset + count_bytes, n_entries * entry_bytes)
            tags = {}
            for index in range(n_entries):
                entry = raw_entries[index * entry_bytes:(index + 1) * entry_bytes]
                tag, datatype = struct.unpack(endian + "HH", entry[:4])
                if datatype not in _TYPE_BYTES:
                    raise ValueError(f"Unsupported TIFF field type {datatype}")
                count = struct.unpack(endian + number_code,
                                      entry[4:4 + offset_bytes])[0]
                length = count * _TYPE_BYTES[datatype]
                value = entry[4 + offset_bytes:]
                if length > offset_bytes:
                    data_offset = struct.unpack(endian + number_code, value)[0]
                    if data_offset + length > file_size:
                        raise ValueError(f"Tag {tag} references data outside TIFF")
                    metadata_ranges.append((data_offset, data_offset + length))
                    if tag in _NEEDED_TAGS:
                        if length > 32 * 1024 * 1024:
                            raise ValueError("Unexpectedly large TIFF layout table")
                        value = read_at(data_offset, length)
                else:
                    value = value[:length]
                if tag in _NEEDED_TAGS:
                    if tag in tags:
                        raise ValueError(f"Duplicate TIFF layout tag {tag}")
                    if datatype not in _INTEGER_TYPES:
                        raise ValueError(f"TIFF layout tag {tag} is not an integer")
                    tags[tag] = struct.unpack(endian + str(count) + _INTEGER_TYPES[datatype],
                                              value)
            next_ifd = struct.unpack(endian + number_code,
                                    read_at(ifd_end - offset_bytes, offset_bytes))[0]
            if next_ifd:
                pending.append(next_ifd)
            pending.extend(tags.get(330, ()))

            def scalar(tag, default=None):
                values = tags.get(tag, (default,))
                if len(values) != 1 or values[0] is None:
                    raise ValueError(f"Missing or non-scalar TIFF layout tag {tag}")
                return values[0]

            width, height = scalar(256), scalar(257)
            tile_width, tile_height = scalar(322), scalar(323)
            if min(width, height, tile_width, tile_height) <= 0:
                raise ValueError("Invalid image or tile dimensions")
            bands = scalar(277, 1)
            if bands != 1:
                raise ValueError("This checker is limited to single-band COGs")
            offsets, byte_counts = tags.get(324, ()), tags.get(325, ())
            expected_tiles = ((width + tile_width - 1) // tile_width *
                              ((height + tile_height - 1) // tile_height))
            if len(offsets) != expected_tiles or len(byte_counts) != expected_tiles:
                raise ValueError("TIFF tile count does not match image dimensions")
            previous_end = -1
            tile_ranges = []
            for offset, byte_count in zip(offsets, byte_counts):
                if offset <= 0 or byte_count <= 0:
                    raise ValueError("Sparse or empty tiles are outside this checker's scope")
                end = offset + byte_count
                if end > file_size:
                    raise ValueError("Tile data extends beyond TIFF file")
                if offset < previous_end:
                    raise ValueError("Tiles overlap or are not stored in row-major order")
                previous_end = end
                tile_ranges.append((offset, end))
            subfile_type = scalar(254, 0)
            directories.append({
                "ifd_offset": ifd_offset, "ifd_end": ifd_end,
                "width": width, "height": height,
                "tile_width": tile_width, "tile_height": tile_height,
                "tile_count": expected_tiles, "compression_tag": scalar(259, 1),
                "predictor_tag": scalar(317, 1), "subfile_type": subfile_type,
                "first_tile_offset": offsets[0], "last_tile_end": previous_end,
                "tile_ranges": tile_ranges,
            })

        first_data = min(directory["first_tile_offset"] for directory in directories)
        if max(end for _, end in metadata_ranges) > first_data:
            raise ValueError("TIFF directories or metadata tables occur after imagery begins")
        all_ranges = sorted(item for directory in directories for item in directory["tile_ranges"])
        if any(current[0] < previous[1] for previous, current in zip(all_ranges, all_ranges[1:])):
            raise ValueError("Tile data from different TIFF directories overlaps")

        imagery = [directory for directory in directories if not directory["subfile_type"] & 4]
        if not imagery or imagery[0]["ifd_offset"] != first_ifd:
            raise ValueError("First TIFF directory is not the main raster image")
        main = imagery[0]
        overviews = imagery[1:]
        if not overviews:
            raise ValueError("No internal overview directories")
        for overview in overviews:
            if not overview["subfile_type"] & 1:
                raise ValueError("Additional imagery is not marked as a reduced-resolution image")
            if overview["width"] >= main["width"] or overview["height"] >= main["height"]:
                raise ValueError("Overview is not smaller than the main image")
        by_data = sorted(imagery, key=lambda directory: directory["first_tile_offset"])
        areas = [directory["width"] * directory["height"] for directory in by_data]
        if any(left >= right for left, right in zip(areas, areas[1:])):
            raise ValueError("Overview imagery is not stored from smallest to largest")
        for left, right in zip(by_data, by_data[1:]):
            if left["last_tile_end"] > right["first_tile_offset"]:
                raise ValueError("Imagery at different resolution levels is interleaved")

        header_text = read_at(0, min(first_ifd, 4096)).decode("ascii", errors="replace")
        structural_lines = [line for line in header_text.splitlines()
                            if line.startswith(("LAYOUT=", "BLOCK_", "KNOWN_INCOMPATIBLE_"))]
        for directory in directories:
            del directory["tile_ranges"]
        return {
            "path": str(path.resolve()), "file_bytes": file_size,
            "format": "BigTIFF" if magic == 43 else "TIFF",
            "byte_order": "little" if endian == "<" else "big",
            "checks_passed": True,
            "scope": "Independent partial structural inspection; not full COG certification",
            "checks": ["IFD and metadata table bounds", "tile bounds and non-overlap",
                       "IFDs and metadata tables before imagery", "row-major tile storage",
                       "internal overviews", "smallest overview imagery first"],
            "gdal_structural_metadata": structural_lines,
            "directories": directories,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args()
    reports = [inspect_cog_layout(path) for path in args.paths]
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
