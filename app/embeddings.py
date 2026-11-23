"""Local text embeddings (sentence-transformers): no API calls, no per-token cost."""

from collections.abc import Sequence

from sentence_transformers import SentenceTransformer

# bge models expect this instruction on short queries (not on the passages they search).
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
BATCH_SIZE = 64


class SentenceTransformerEmbedder:
    """Unit-length vectors, so cosine similarity is a dot product. CPU only."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")
        self._query_instruction = BGE_QUERY_INSTRUCTION if "bge" in model_name.lower() else ""

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(self._query_instruction + text, normalize_embeddings=True)
        return vector.tolist()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=True
        )
        return vectors.tolist()
