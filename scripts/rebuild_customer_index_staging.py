
from pathlib import Path

from openai import OpenAI

from backend.rag.customer_index import (
    build_staging,
    verify_index,
    validated_documents,
)

MODEL = "text-embedding-3-small"
STAGING = Path("local_customer_index.staging-20261103")


def main():
    if STAGING.exists():
        raise FileExistsError(
            f"Staging directory already exists: {STAGING}"
        )

    docs = validated_documents()
    print("Approved passages:", len(docs))

    if len(docs) != 13:
        raise RuntimeError(
            f"Expected 13 approved passages, found {len(docs)}"
        )

    client = OpenAI()

    def embed_documents(texts):
        response = client.embeddings.create(
            model=MODEL,
            input=texts,
        )

        ordered = sorted(
            response.data,
            key=lambda item: item.index,
        )

        if len(ordered) != len(texts):
            raise RuntimeError("Embedding count mismatch")

        return [item.embedding for item in ordered]

    stage = build_staging(
        embed_documents,
        staging_dir=STAGING,
        model=MODEL,
    )

    manifest, index = verify_index(stage)

    if manifest["model"] != MODEL:
        raise RuntimeError("Embedding model mismatch")

    if index.d != 1536:
        raise RuntimeError("Unexpected embedding dimensions")

    if index.ntotal != 13:
        raise RuntimeError("Unexpected vector count")

    print("\nSTAGING BUILD VERIFIED")
    print("Directory:", stage)
    print("Model:", manifest["model"])
    print("Passages:", len(manifest["chunks"]))
    print("Dimensions:", index.d)
    print("Vectors:", index.ntotal)


if __name__ == "__main__":
    main()
