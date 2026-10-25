"""In-process GGUF embedding model (stage 2: vector indexing).

Loads a local GGUF with llama-cpp-python on demand (CPU by default — the GPU is
occupied by the chat model) and writes float32 vectors into the index. The
embedding model name and dimension are recorded in the index meta; mixing models
or dimensions refuses to proceed.
"""
import os

EMBED_MODEL_DEFAULT = "~/models/nomic-embed-text-v1.5.Q8_0.gguf"


class EmbedModelUnavailable(RuntimeError):
    pass


class EmbedModelMismatch(RuntimeError):
    pass


def embed_text(doc: dict) -> str:
    """The text embedded for a doc: title + abstract + summary (nothing else)."""
    parts = [doc.get("title") or "", doc.get("abstract") or "", doc.get("summary") or ""]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


class Embedder:
    def __init__(self, model_path: str, device: str = "cpu"):
        self.model_path = os.path.expanduser(model_path or "")
        if not self.model_path or not os.path.isfile(self.model_path):
            raise EmbedModelUnavailable(
                f"embedding model not found: {model_path!r} (set EMBED_MODEL to a GGUF path)")
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise EmbedModelUnavailable(
                "llama-cpp-python is not installed; install the extra: uv pip install 'zotero-rag[embed]'") from e
        self._llm = Llama(model_path=self.model_path, embedding=True, n_ctx=2048,
                          device=device, verbose=False)
        self.model_name = os.path.basename(self.model_path)
        self.dim = None  # learned from the first batch

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out = self._llm.create_embedding(texts)["data"]
        vecs = [d["embedding"] for d in out]
        if self.dim is None and vecs:
            self.dim = len(vecs[0])
        return vecs


def embed_missing(index, embedder, batch: int = 32) -> int:
    """Embed every doc without a vector. Returns the number embedded."""
    model = index.meta_get("embed_model")
    dim = index.meta_get("embed_dim")
    if model is not None and model != getattr(embedder, "model_name", ""):
        raise EmbedModelMismatch(
            f"index was built with embedding model {model!r} but the embedder is "
            f"{getattr(embedder, 'model_name', '?')!r}; rebuild the index to switch models")
    if dim is not None and getattr(embedder, "dim", None) and int(dim) != int(embedder.dim):
        raise EmbedModelMismatch(
            f"index vectors are {dim}-dim but the embedder produces {embedder.dim}-dim; "
            f"rebuild the index to switch models")

    keys = index.keys_without_vector()
    n = 0
    got_dim = getattr(embedder, "dim", None)
    for i in range(0, len(keys), batch):
        chunk = keys[i:i + batch]
        docs = [index.get(k) for k in chunk]
        vecs = embedder.embed([embed_text(d) for d in docs])
        if dim is not None and len(vecs[0]) != int(dim):
            raise EmbedModelMismatch(
                f"index vectors are {dim}-dim but the embedder produced {len(vecs[0])}-dim; "
                f"rebuild the index to switch models")
        got_dim = got_dim or len(vecs[0])
        for k, v in zip(chunk, vecs):
            index.set_vector(k, v)
        n += len(chunk)
    if model is None:
        index.meta_set("embed_model", getattr(embedder, "model_name", ""))
        index.meta_set("embed_dim", str(got_dim or 0))
    return n
