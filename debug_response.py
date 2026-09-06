"""
Standalone diagnostic: bypasses the graph entirely and prints the RAW
response object from the model, so we can see exactly what came back
(content, tool_calls, reasoning_content, everything) instead of just
the one field main.py happens to print.

Run this from inside your terminal_agent project directory:
    python debug_response.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from llm import get_llm
from tools import LOCAL_TOOLS
from langchain_core.messages import HumanMessage, SystemMessage
from agent import SYSTEM_PROMPT

llm = get_llm().bind_tools(LOCAL_TOOLS)
messages = [
    SystemMessage(content=SYSTEM_PROMPT),
    HumanMessage(content='add a third line to output.txt that says "done"'),
]
response = llm.invoke(messages)

print("=== content ===")
print(repr(response.content))
print("\n=== tool_calls ===")
print(response.tool_calls)
print("\n=== additional_kwargs ===")
print(response.additional_kwargs)
print("\n=== response_metadata ===")
print(response.response_metadata)