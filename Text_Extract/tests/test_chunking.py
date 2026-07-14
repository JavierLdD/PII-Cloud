from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chunking.chunker import ChunkingConfig, build_text_chunks  # noqa: E402
from common.models import SourceBlock  # noqa: E402


def source_block(
    block_id: str,
    text: str,
    page_number: int = 1,
    block_index: int = 1,
) -> SourceBlock:
    return SourceBlock(
        source_block_id=block_id,
        run_id="run-1",
        file_id="file-1",
        source_type="local",
        source_uri="local:///tmp/doc.pdf",
        file_name="doc.pdf",
        original_path="/tmp/doc.pdf",
        page_number=page_number,
        page_index=page_number - 1,
        block_index=block_index,
        method="pymupdf",
        routing_reason="embedded_text",
        block_type="text",
        text=text,
        bbox=[1, 2, 3, 4],
    )


def test_chunking_never_mixes_pdf_pages():
    chunks = build_text_chunks(
        [
            source_block("p1-b1", "pagina uno", page_number=1),
            source_block("p2-b1", "pagina dos", page_number=2),
        ]
    )

    assert len(chunks) == 2
    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [
        (1, 1),
        (2, 2),
    ]


def test_chunking_preserves_source_map_offsets_and_bbox():
    chunks = build_text_chunks(
        [
            source_block("p1-b1", "Nombre: Javier", block_index=1),
            source_block("p1-b2", "RUN 19.915.845-7", block_index=2),
        ]
    )

    assert chunks[0].text == "nombre: javier run 19.915.845-7"
    source_map = chunks[0].source_map
    assert source_map["file_id"] == "file-1"
    assert source_map["source_type"] == "local"
    assert source_map["source_uri"] == "local:///tmp/doc.pdf"
    assert source_map["page_start"] == 1
    assert [segment["source_block_id"] for segment in source_map["segments"]] == [
        "p1-b1",
        "p1-b2",
    ]
    assert source_map["segments"][0]["chunk_text_start"] == 0
    assert source_map["segments"][0]["source_text_start"] == 0
    assert source_map["segments"][0]["bbox"] == [1, 2, 3, 4]


def test_chunking_normalizes_case_and_whitespace_before_staging():
    chunks = build_text_chunks(
        [
            source_block(
                "p1-b1",
                " Nombre:\nAna \r\n\t Direccion:   Alameda 123 ",
                block_index=1,
            )
        ]
    )

    assert chunks[0].text == "nombre: ana direccion: alameda 123"
    assert "\n" not in chunks[0].text
    assert "\r" not in chunks[0].text
    assert "\t" not in chunks[0].text
    assert chunks[0].text_hash_sha256 == hashlib.sha256(
        b"nombre: ana direccion: alameda 123"
    ).hexdigest()
    segment = chunks[0].source_map["segments"][0]
    assert segment["chunk_text_start"] == 0
    assert segment["chunk_text_end"] == len(chunks[0].text)
    assert segment["source_text_start"] == 0
    assert segment["source_text_end"] == len(chunks[0].text)


def test_chunking_joins_normalized_blocks_with_spaces():
    chunks = build_text_chunks(
        [
            source_block("p1-b1", "Primera\nLinea", block_index=1),
            source_block("p1-b2", "SEGUNDA\tLINEA", block_index=2),
        ]
    )

    assert chunks[0].text == "primera linea segunda linea"
    assert "\n" not in chunks[0].text
    segments = chunks[0].source_map["segments"]
    assert segments[0]["chunk_text_start"] == 0
    assert segments[0]["chunk_text_end"] == len("primera linea")
    assert segments[1]["chunk_text_start"] == len("primera linea ")
    assert segments[1]["chunk_text_end"] == len(chunks[0].text)


def test_small_trailing_chunk_is_merged_when_it_fits_under_max():
    chunks = build_text_chunks(
        [
            source_block("p1-b1", "x" * 800, block_index=1),
            source_block("p1-b2", "y" * 100, block_index=2),
        ],
        ChunkingConfig(
            target_chars=700,
            max_chars=1000,
            min_chars=400,
            overlap_chars=0,
        ),
    )

    assert len(chunks) == 1
    assert len(chunks[0].text) == 901


def test_small_trailing_chunk_is_rebalanced_when_merge_exceeds_max():
    chunks = build_text_chunks(
        [
            source_block("p1-b1", "x" * 350, block_index=1),
            source_block("p1-b2", "y" * 350, block_index=2),
            source_block("p1-b3", "z" * 100, block_index=3),
        ],
        ChunkingConfig(
            target_chars=650,
            max_chars=750,
            min_chars=400,
            overlap_chars=0,
        ),
    )

    assert len(chunks) == 2
    assert [len(chunk.text) for chunk in chunks] == [350, 451]


def test_overlap_does_not_make_tiny_new_text_count_as_a_valid_chunk():
    chunks = build_text_chunks(
        [
            source_block("p1-b1", "x" * 600, block_index=1),
            source_block("p1-b2", "y" * 600, block_index=2),
            source_block("p1-b3", "z" * 100, block_index=3),
        ],
        ChunkingConfig(
            target_chars=700,
            max_chars=1000,
            min_chars=400,
            overlap_chars=500,
        ),
    )

    assert len(chunks) == 2
    assert chunks[1].source_map["segments"][0]["is_overlap"] is True
    new_segments = [
        segment
        for segment in chunks[1].source_map["segments"]
        if not segment["is_overlap"]
    ]
    assert [segment["source_block_id"] for segment in new_segments] == [
        "p1-b2",
        "p1-b3",
    ]
