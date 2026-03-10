from __future__ import annotations

from rank_bm25 import BM25Okapi


class RagWikivoyageService:
    def chunk_text(self, text: str, chunk_size: int = 450) -> list[str]:
        cleaned = " ".join(text.split())
        if not cleaned:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(cleaned):
            chunks.append(cleaned[start:start + chunk_size])
            start += chunk_size - 50
        return chunks

    def retrieve_snippets(self, text: str, query: str, top_k: int = 3) -> list[str]:
        chunks = self.chunk_text(text)
        if not chunks:
            return []
        tokenized_chunks = [chunk.lower().split() for chunk in chunks]
        bm25 = BM25Okapi(tokenized_chunks)
        scores = bm25.get_scores(query.lower().split())
        ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
        return [chunk for chunk, _ in ranked[:top_k]]
