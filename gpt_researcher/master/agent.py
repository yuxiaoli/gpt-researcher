import asyncio
import time
from gpt_researcher.config import Config
from gpt_researcher.master.functions import *
from gpt_researcher.context.compression import ContextCompressor
from gpt_researcher.memory import Memory

from langchain_text_splitters import TokenTextSplitter
from langchain.callbacks import get_openai_callback
import tiktoken
def count_tokens(string: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding("cl100k_base")
    num_tokens = len(encoding.encode(string))
    return num_tokens
import random


class GPTResearcher:
    """
    GPT Researcher
    """
    def __init__(
        self, 
        query, 
        report_type="research_report", 
        source_urls=None, 
        config_path=None, 
        websocket=None, 
        prompt_token_limit: int=10000, 
        total_words: int=1000, 
    ):
        """
        Initialize the GPT Researcher class.
        Args:
            query:
            report_type:
            config_path:
            websocket:
        """
        self.query = query
        self.agent = None
        self.role = None
        self.report_type = report_type
        self.websocket = websocket
        self.cfg = Config(
            config_path, 
            prompt_token_limit=prompt_token_limit, 
            total_words=total_words
        )
        self.retriever = get_retriever(self.cfg.retriever)
        self.context = []
        self.source_urls = source_urls
        self.memory = Memory(self.cfg.embedding_provider)
        self.visited_urls = set()

    async def run(self):
        """
        Runs the GPT Researcher
        Returns:
            Report
        """
        print(f"🔎 Running research for '{self.query}'...")
        # Generate Agent
        self.agent, self.role = await choose_agent(self.query, self.cfg)
        await stream_output("logs", self.agent, self.websocket)

        # If specified, the researcher will use the given urls as the context for the research.
        if self.source_urls:
            self.context = await self.get_context_by_urls(self.source_urls)
        else:
            self.context = await self.get_context_by_search(self.query)

        # Write Research Report
        if self.report_type == "custom_report":
            self.role = self.cfg.agent_role if self.cfg.agent_role else self.role
        await stream_output("logs", f"✍️ Writing {self.report_type} for research task: {self.query}...", self.websocket)
        # Compress context
        smart_token_total = 16385   # gpt-3.5
        # smart_token_total = 8192  # gpt-4
        prompt_token_limit = min(self.cfg.smart_token_max - self.cfg.total_words * 2, self.cfg.prompt_token_limit)
        # total_words = 3000
        buffer_tokens = 512
        context_token_limit = prompt_token_limit - buffer_tokens
        # text_splitter = TokenTextSplitter(chunk_size=context_token_limit, chunk_overlap=0)
        # # print(type(self.context))
        # context_str = text_splitter.split_text(str(self.context))[0]
        while count_tokens(str(self.context)) > context_token_limit:
            # self.context.pop(0)
            self.context.pop(random.randrange(len(self.context)))
        prompt_tokens = count_tokens(str(self.context)) + buffer_tokens
        report = await generate_report(query=self.query, context=self.context,
                                   agent_role_prompt=self.role, report_type=self.report_type,
                                   websocket=self.websocket, cfg=self.cfg)
        time.sleep(2)
        print(f"prompt_tokens: {prompt_tokens}")
        print(f"total_words: {self.cfg.total_words}")
        # print(report)
        completion_tokens = count_tokens(report)
        print(f"completion_tokens (report): {completion_tokens}")
        await stream_output("usage", json.dumps({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "smart_llm_model": self.cfg.smart_llm_model,
        }), self.websocket)
        return report

    async def get_context_by_urls(self, urls):
        """
            Scrapes and compresses the context from the given urls
        """
        new_search_urls = await self.get_new_urls(urls)
        await stream_output("logs",
                            f"🧠 I will conduct my research based on the following urls: {new_search_urls}...",
                            self.websocket)
        scraped_sites = scrape_urls(new_search_urls, self.cfg)
        return await self.get_similar_content_by_query(self.query, scraped_sites)

    async def get_context_by_search(self, query):
        """
           Generates the context for the research task by searching the query and scraping the results
        Returns:
            context: List of context
        """
        context = []
        # Generate Sub-Queries including original query
        sub_queries = await get_sub_queries(query, self.role, self.cfg) + [query]
        await stream_output("logs",
                            f"🧠 I will conduct my research based on the following queries: {sub_queries}...",
                            self.websocket)

        # Using asyncio.gather to process the sub_queries asynchronously
        context = await asyncio.gather(*[self.process_sub_query(sub_query) for sub_query in sub_queries])
        return context
    
    async def process_sub_query(self, sub_query: str):
        """Takes in a sub query and scrapes urls based on it and gathers context.

        Args:
            sub_query (str): The sub-query generated from the original query

        Returns:
            str: The context gathered from search
        """
        await stream_output("logs", f"\n🔎 Running research for '{sub_query}'...", self.websocket)
        scraped_sites = await self.scrape_sites_by_query(sub_query)
        content = await self.get_similar_content_by_query(sub_query, scraped_sites)
        await stream_output("logs", f"📃 {content}", self.websocket)
        return content

    async def get_new_urls(self, url_set_input):
        """ Gets the new urls from the given url set.
        Args: url_set_input (set[str]): The url set to get the new urls from
        Returns: list[str]: The new urls from the given url set
        """

        new_urls = []
        for url in url_set_input:
            if url not in self.visited_urls:
                await stream_output("logs", f"✅ Adding source url to research: {url}\n", self.websocket)

                self.visited_urls.add(url)
                new_urls.append(url)

        return new_urls

    async def scrape_sites_by_query(self, sub_query):
        """
        Runs a sub-query
        Args:
            sub_query:

        Returns:
            Summary
        """
        # Get Urls
        retriever = self.retriever(sub_query)
        search_results = retriever.search(max_results=self.cfg.max_search_results_per_query)
        new_search_urls = await self.get_new_urls([url.get("href") for url in search_results])

        # Scrape Urls
        # await stream_output("logs", f"📝Scraping urls {new_search_urls}...\n", self.websocket)
        await stream_output("logs", f"🤔Researching for relevant information...\n", self.websocket)
        scraped_content_results = scrape_urls(new_search_urls, self.cfg)
        return scraped_content_results

    async def get_similar_content_by_query(self, query, pages):
        await stream_output("logs", f"📃 Getting relevant content based on query: {query}...", self.websocket)
        # Summarize Raw Data
        context_compressor = ContextCompressor(documents=pages, embeddings=self.memory.get_embeddings())
        # Run Tasks
        return context_compressor.get_context(query, max_results=4)
        return context_compressor.get_context(query, max_results=8)

