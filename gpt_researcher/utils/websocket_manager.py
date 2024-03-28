# connect any client to gpt-researcher using websocket
import asyncio
import datetime
from typing import List, Dict
from fastapi import WebSocket
from gpt_researcher.master.agent import GPTResearcher
import os

class WebSocketManager:
    """Manage websockets"""
    def __init__(self):
        """Initialize the WebSocketManager class."""
        self.active_connections: List[WebSocket] = []
        self.sender_tasks: Dict[WebSocket, asyncio.Task] = {}
        self.message_queues: Dict[WebSocket, asyncio.Queue] = {}

    async def start_sender(self, websocket: WebSocket):
        """Start the sender task."""
        queue = self.message_queues.get(websocket)
        if not queue:
            return

        while True:
            message = await queue.get()
            if websocket in self.active_connections:
                try:
                    await websocket.send_text(message)
                except:
                    break
            else:
                break

    async def connect(self, websocket: WebSocket):
        """Connect a websocket."""
        await websocket.accept()
        self.active_connections.append(websocket)
        self.message_queues[websocket] = asyncio.Queue()
        self.sender_tasks[websocket] = asyncio.create_task(self.start_sender(websocket))

    async def disconnect(self, websocket: WebSocket):
        """Disconnect a websocket."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            self.sender_tasks[websocket].cancel()
            await self.message_queues[websocket].put(None)
            del self.sender_tasks[websocket]
            del self.message_queues[websocket]

    async def start_streaming(self, task, report_type, websocket, source_urls: list=None, config_path: str=None, prompt_token_limit: int=10000, total_words: int=1000):
        """Start streaming the output."""
        report = await run_agent(task, report_type, websocket, source_urls, config_path, prompt_token_limit, total_words)
        return report


async def run_agent(task, report_type, websocket, source_urls: list=None, config_path: str=None, prompt_token_limit: int=10000, total_words: int=1000):
    """Run the agent."""
    # measure time
    start_time = datetime.datetime.now()
    # add customized JSON config file path here
    # config_path = None
    # config_path = os.path.join("configs", "config-low.json")
    # run agent
    researcher = GPTResearcher(
        query=task, 
        report_type=report_type, 
        # source_urls=None, 
        source_urls=source_urls, 
        config_path=config_path, 
        websocket=websocket, 
        prompt_token_limit=prompt_token_limit, 
        total_words=total_words, 
    )
    print("STATUS")
    print("=" * 24)
    print(vars(researcher))
    print(f"CONFIG (loading config from {config_path})")
    print("=" * 24)
    for key, value in vars(researcher.cfg).items():
        print(f"{key}: {value}")
    # return
    report = await researcher.run()
    # measure time
    end_time = datetime.datetime.now()
    await websocket.send_json({"type": "logs", "output": f"\nTotal run time: {end_time - start_time}\n"})

    return report
