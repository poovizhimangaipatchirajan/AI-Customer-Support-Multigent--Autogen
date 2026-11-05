"""Read-only FixFlow delivery RAG diagnostic. Does not access or modify SQLite.

Uses one OpenAI embedding request via HybridRetriever.search.
Run from project root: python scripts/diagnose_delivery_rag.py
"""
from backend.rag.hybrid import HybridRetriever

QUERY = 'what is the delivery timeline ?'


def main():
    retriever = HybridRetriever()
    results = retriever.search(QUERY, k=8)
    print('Query:', QUERY)
    print('Results:', len(results))
    for number, passage in enumerate(results, 1):
        print(f'\n#{number} id={passage.get("id")} source={passage.get("source")} '
              f'page={passage.get("page")} visibility={passage.get("visibility")}')
        text = ' '.join(str(passage.get('text', '')).split())
        print('Excerpt:', text[:650])
    print('\nDiagnostic only: ranked retrieval is NOT proof of policy support.')
    print('If the relevant shipping policy is absent or unclear, inspect the approved PDF and index.')


if __name__ == '__main__':
    main()
