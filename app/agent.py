import os
import asyncio
import json
import httpx
from bs4 import BeautifulSoup
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import Chroma

class HeavyAgentRAGPipeline:
    def __init__(self, publish_callback=None):
        self.embeddings = OpenAIEmbeddings(api_key=os.getenv("OPENAI_API_KEY"))
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.2, api_key=os.getenv("OPENAI_API_KEY"))
        self.publish = publish_callback or self._default_publish

    async def _default_publish(self, step_name: str, status: str, payload: dict):
        print(f"[{step_name}] {status}: {payload}")

    async def scrape_target_urls(self, urls: list[str]) -> list[dict]:
        """Scrape web content continuously without worrying about serverless timeouts."""
        await self.publish("web_scraper", "started", {"urls": urls})
        scraped_docs = []
        
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for url in urls:
                try:
                    await self.publish("web_scraper", "scraping_page", {"url": url})
                    res = await client.get(url)
                    if res.status_code == 200:
                        soup = BeautifulSoup(res.text, "html.parser")
                        # Strip script and style tags
                        for script in soup(["script", "style"]):
                            script.decompose()
                        text = soup.get_text(separator=" ", strip=True)
                        scraped_docs.append({"url": url, "text": text[:10000]}) # limit per page
                except Exception as e:
                    await self.publish("web_scraper", "error", {"url": url, "error": str(e)})
        
        await self.publish("web_scraper", "completed", {"docs_count": len(scraped_docs)})
        return scraped_docs

    async def recursive_rag_query(self, query: str, docs: list[dict]) -> str:
        """Vector index creation + recursive multi-step chunk retrieval."""
        await self.publish("rag_pipeline", "indexing", {"count": len(docs)})
        
        texts = [doc["text"] for doc in docs if doc.get("text")]
        metadatas = [{"source": doc["url"]} for doc in docs if doc.get("text")]

        if not texts:
            return "No text available for search."

        # In-memory Chroma instance for processing target docs
        vectorstore = await asyncio.to_thread(
            Chroma.from_texts,
            texts=texts,
            embedding=self.embeddings,
            metadatas=metadatas
        )

        await self.publish("rag_pipeline", "searching", {"query": query})
        
        # Multi-query search logic
        retriever = vectorstore.as_retriever(search_kwargs={"k": 4})
        retrieved_docs = await asyncio.to_thread(retriever.invoke, query)
        
        context = "\n\n---\n\n".join([d.page_content for d in retrieved_docs])

        await self.publish("agent_reasoning", "generating_response", {})
        
        prompt = f"""You are an autonomous research agent. Synthesize the collected background context to answer the user query.

Query: {query}

Context:
{context}

Provide a detailed, structured, comprehensive report."""
        
        response = await self.llm.ainvoke(prompt)
        return response.content

    async def run(self, query: str, seed_urls: list[str]) -> str:
        """Master execution loop for the agent."""
        await self.publish("agent_loop", "started", {"query": query})
        
        # Step 1: Deep Scrape
        scraped_docs = await self.scrape_target_urls(seed_urls)
        
        # Step 2: Recursive RAG Analysis
        result = await self.recursive_rag_query(query, scraped_docs)
        
        await self.publish("agent_loop", "finished", {"result_preview": result[:150]})
        return result