import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import tiktoken
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
from modules.llm.utils.embedding import initialize_embedding
from modules.llm.const import (
    MEMORY_DIR,
    MEMORY_EMBEDDING_MODEL,
    MAX_FETCH_LIMIT,
    CANDIDATE_MULTIPLIER,
    MEMORY_TOKEN_BUDGET,
    MEMORY_RELEVANCE_THRESHOLD,
    MEMORY_HYBRID_WEIGHTS
)
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

class MemoryManager:
    def __init__(self):
        self.memory_root = MEMORY_DIR
        self.global_path = self.memory_root / "global"
        self.repo_path = self.memory_root / "repo"
        self.vector_db_path = self.memory_root / "chroma_db"

        # Ensure base directories exist
        self.global_path.mkdir(parents=True, exist_ok=True)
        self.repo_path.mkdir(parents=True, exist_ok=True)

        # Initialize Embeddings
        provider, model_name = MEMORY_EMBEDDING_MODEL
        self.embedding_fn = initialize_embedding(provider, model_name)

        # Initialize Indices
        self.vector_store = None
        self.bm25_retriever = None
        self._init_indices()

    def _init_indices(self):
        """Initialize the vector and keyword indices."""
        self._init_vector_store()

        # Try to rehydrate BM25 from the existing vector store to ensure it's always available
        if self.vector_store and self.bm25_retriever is None:
            try:
                data = self.vector_store.get()
                if data and 'documents' in data and data['documents']:
                    from langchain_core.documents import Document
                    docs = [
                        Document(page_content=doc, metadata=meta)
                        for doc, meta in zip(data['documents'], data['metadatas'] or [{}] * len(data['documents']))
                    ]
                    self.bm25_retriever = BM25Retriever.from_documents(docs)
                    self.bm25_retriever.k = MAX_FETCH_LIMIT
                    logger.info("✅ BM25 index rehydrated from Vector Store (%d chunks).", len(docs))
            except Exception as e:
                logger.warning("Optional BM25 rehydration skipped: %s", e)

    def _init_vector_store(self):
        """Initialize the Chroma vector store."""
        try:
            self.vector_store = Chroma(
                persist_directory=str(self.vector_db_path),
                embedding_function=self.embedding_fn,
                collection_name="agent_memory"
            )
        except Exception as e:
            logger.error("Failed to initialize ChromaDB: %s", e)
            self.vector_store = None

    def sync_memory(self):
        """
        Reads all Markdown files from the memory directory and re-indexes them.
        """
        logger.info("🔄 Syncing memory from Git files to Vector DB...")

        # Ensure base directory exists
        self.memory_root.mkdir(parents=True, exist_ok=True)

        documents = []

        # Markdown Splitter
        headers_to_split_on = [
            ("#", "category"),
            ("##", "topic"),
            ("###", "subtopic"),
        ]
        markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)

        # 1. Collect files to index
        files_to_index = []

        for ext in ["md", "markdown"]:
            for path in self.memory_root.rglob(f"*.{ext}"):
                # Exclude chromadb dir
                if str(self.vector_db_path) in str(path):
                    continue

                # Determine type and repo_name based on path relative to memory_root
                doc_type = "general"
                repo_name = None
                try:
                    rel_path = path.relative_to(self.memory_root)
                    if rel_path.parts[0] == "global":
                        doc_type = "global"
                    elif rel_path.parts[0] in ("repo", "repos"):
                        doc_type = "repo"
                        # Extract repo name if path is memory/repo/{repo_name}/{category}.md
                        if len(rel_path.parts) >= 2:
                            repo_name = rel_path.parts[1]
                except:
                    pass

                files_to_index.append({
                    "path": path,
                    "type": doc_type,
                    "repo_name": repo_name
                })

        for file_info in files_to_index:
            try:
                path = file_info["path"]
                if path.is_file():
                    text = path.read_text()

                    # Split text by logic (Markdown headers)
                    splits = markdown_splitter.split_text(text)

                    # Tag metadata
                    for split in splits:
                        split.metadata["source"] = file_info["type"]
                        split.metadata["file_path"] = str(path)
                        split.metadata["filename"] = path.name
                        if file_info["repo_name"]:
                            split.metadata["repo_name"] = file_info["repo_name"]
                        documents.append(split)
            except Exception as e:
                logger.error("Error indexing file %s: %s", file_info['path'], e)

        # 2. Update Vector DB
        if documents:
            try:
                # Reset collection for freshness
                if self.vector_store:
                    self.vector_store.delete_collection()

                self.vector_store = Chroma.from_documents(
                    documents=documents,
                    embedding=self.embedding_fn,
                    persist_directory=str(self.vector_db_path),
                    collection_name="agent_memory"
                )

                # Update BM25 Retriever
                self.bm25_retriever = BM25Retriever.from_documents(documents)
                self.bm25_retriever.k = MAX_FETCH_LIMIT

                logger.info("🧠 MEMORY SYNC COMPLETE: %d chunks | %d files | Hybrid Index ✅", len(documents), len(files_to_index))
                return f"Successfully indexed {len(documents)} chunks from {len(files_to_index)} files."
            except Exception as e:
                error_msg = f"❌ Failed to update vector store: {e}"
                logger.error(error_msg)
                return error_msg
        else:
            logger.warning("⚠️ No memory files found to index.")
            return "No memory files found to index."

    def search(self, query: str, limit: int = 5, token_budget: int = MEMORY_TOKEN_BUDGET) -> List[str]:
        """
        OpenClaw-style Hybrid Search:
        1. Oversamples candidates (k=30).
        2. Filters vector matches by relevance threshold.
        3. Ranks via Weighted Linear Sum (0.7 Vector / 0.3 Keyword).
        4. Packs documents until token_budget is reached or optional limit is met.
        """
        if not self.vector_store:
            raise ValueError("Memory Search Failed: Vector Store not initialized.")
        if self.bm25_retriever is None:
            raise ValueError("Memory Search Failed: BM25/Keyword index not initialized.")

        try:
            # 1. Fetch Vector candidates with scores to apply relevance threshold
            # Dynamic Fetch K: we fetch CANDIDATE_MULTIPLIER * the requested limit to ensure a high-quality hybrid pool.
            fetch_k = min(CANDIDATE_MULTIPLIER * limit, MAX_FETCH_LIMIT)

            vector_results_with_scores = self.vector_store.similarity_search_with_score(
                query, k=fetch_k
            )

            # Filter results by relevance threshold
            relevant_vector_docs = [
                doc for doc, score in vector_results_with_scores
                if score < MEMORY_RELEVANCE_THRESHOLD
            ]

            if not relevant_vector_docs and self.bm25_retriever is None:
                return []

            # 2. Ensemble with Keyword Search
            from langchain_core.retrievers import BaseRetriever
            from langchain_core.callbacks import CallbackManagerForRetrieverRun
            from langchain_core.documents import Document

            class FixedRetriever(BaseRetriever):
                docs: List[Document]
                def _get_relevant_documents(self, query: str, *, run_manager: Optional[CallbackManagerForRetrieverRun] = None) -> List[Document]:
                    return self.docs

            vector_retriever = FixedRetriever(docs=relevant_vector_docs)

            self.bm25_retriever.k = fetch_k
            ensemble_retriever = EnsembleRetriever(
                retrievers=[self.bm25_retriever, vector_retriever],
                weights=MEMORY_HYBRID_WEIGHTS
            )
            raw_results = ensemble_retriever.invoke(query)

            # 3. Greedy Packing by Token Budget
            final_results = []
            current_tokens = 0

            # Using tiktoken for accurate budget management (cl100k_base is standard for GPT-4)
            encoding = tiktoken.get_encoding("cl100k_base")

            for doc in raw_results:
                source = doc.metadata.get("source", "unknown")
                filename = doc.metadata.get("filename", "unknown")
                repo_name = doc.metadata.get("repo_name")

                repo_tag = ""
                if repo_name:
                    repo_tag = f"[repo:{repo_name}] "
                elif source == "global":
                    repo_tag = "[global] "

                content = f"{repo_tag}[{source}] {filename}:\n{doc.page_content}"

                token_count = len(encoding.encode(content))

                if current_tokens + token_count > token_budget:
                    logger.debug("Token budget reached (%d tokens). Stopping pack.", current_tokens)
                    break

                final_results.append(content)
                current_tokens += token_count

                if limit and len(final_results) >= limit:
                    logger.debug("Requested limit (%d) reached. Stopping pack.", limit)
                    break

            return final_results

        except Exception as e:
            logger.error("Hybrid token-budget search error: %s", e)
            raise e


# Singleton instance
_memory_manager = None

def get_memory_manager() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager
