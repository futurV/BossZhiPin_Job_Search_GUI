"""``vectorization.py`` 的单测。

embed_pdf 的端到端要跑真的 sentence-transformers + chromadb，那个属于 integration
test 范畴；这里只测纯函数（split_text / file_hash），保证基础切片/缓存键稳定。
"""
from __future__ import annotations

import pytest

from boss_zhipin import vectorization
from boss_zhipin.vectorization import split_text, text_hash


# ---------- split_text ----------

class TestSplitText:
    def test_short_text_single_chunk(self):
        chunks = split_text("短文本", chunk_size=1000, chunk_overlap=200)
        assert chunks == ["短文本"]

    def test_long_text_chunked_with_overlap(self):
        text = "ABCDEFGHIJ" * 100  # 1000 字符
        chunks = split_text(text, chunk_size=300, chunk_overlap=50)
        # 至少切成多块
        assert len(chunks) > 1
        # 每块不超过 chunk_size
        for c in chunks:
            assert len(c) <= 300
        # 相邻块之间有 overlap（不严格断在边界）
        # 验证：把所有 chunk concat 起来去重后包含原始 text
        assert text in "".join(chunks) or len("".join(chunks)) >= len(text)

    def test_empty_text_returns_empty(self):
        assert split_text("", chunk_size=1000, chunk_overlap=200) == []

    def test_chunk_size_must_exceed_overlap(self):
        with pytest.raises(ValueError):
            split_text("anything", chunk_size=100, chunk_overlap=100)
        with pytest.raises(ValueError):
            split_text("anything", chunk_size=50, chunk_overlap=100)

    def test_whitespace_only_chunks_dropped(self):
        # chunk 全是空白时会被 .strip() 掉
        text = "   AAA   "
        chunks = split_text(text, chunk_size=3, chunk_overlap=1)
        # 没有任何 chunk 是纯空白
        assert all(c.strip() for c in chunks)

    def test_chinese_text_chunked_correctly(self):
        # 中文（多字节）也按字符数切，不该按字节
        text = "求职者简历内容" * 100
        chunks = split_text(text, chunk_size=50, chunk_overlap=10)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 50
            assert "求职者" in c or "简历" in c or "内容" in c


# ---------- text_hash ----------

class TestTextHash:
    def test_same_content_same_hash(self):
        assert text_hash("hello world") == text_hash("hello world")

    def test_different_content_different_hash(self):
        assert text_hash("hello world") != text_hash("hello world!")

    def test_hash_is_md5_hex(self):
        h = text_hash("x")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


def test_embedder_uses_configured_model_cache(monkeypatch, tmp_path):
    calls: list[tuple[str, dict]] = []
    fake_model = object()

    def fake_sentence_transformer(name: str, **kwargs):
        calls.append((name, kwargs))
        if kwargs.get("local_files_only"):
            raise OSError("not cached")
        return fake_model

    monkeypatch.setenv("BOSS_MODEL_CACHE_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(vectorization, "SentenceTransformer", fake_sentence_transformer)
    monkeypatch.setattr(vectorization, "_embedder", None)
    monkeypatch.setattr(vectorization, "_embedder_cache_dir", None)

    assert vectorization._get_embedder() is fake_model
    project_cache = str((tmp_path / "models").resolve())
    assert calls[0][1]["cache_folder"] == str(vectorization.hf_user_cache_dir())
    assert calls[0][1]["local_files_only"] is True
    assert calls[1][1]["cache_folder"] == project_cache
    assert calls[1][1]["local_files_only"] is True
    assert calls[2][1]["cache_folder"] == project_cache
    assert "local_files_only" not in calls[2][1]
