from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, AsyncGenerator
import os
import uuid
import time
import sys
import asyncio
import logging
import json
from pathlib import Path
import random

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path to import agent module
sys.path.append(str(Path(__file__).parent.parent))
from src.agt.agent import graph as agt_graph, VaaniState
from langchain_core.messages import HumanMessage, AIMessage

# Import model clients
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq

# Import the react_agent modules
from src.react_agent import graph as react_graph
from src.react_agent.state import InputState
from src.react_agent.configuration import Configuration
from src.react_agent.utils import load_chat_model

# Add this import for Anthropic exceptions
import anthropic

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Setup app
app = FastAPI(title="Vaani.pro API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://vanni-test-frontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Configuration
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Verify API keys are present
required_keys = {
    "OPENAI_API_KEY": "OpenAI models",
    "GOOGLE_API_KEY": "Google Gemini models",
    "ANTHROPIC_API_KEY": "Anthropic Claude models",
    "GROQ_API_KEY": "Groq models"
}

for key, model_name in required_keys.items():
    if not os.getenv(key):
        logger.warning(f"Warning: {key} is not set. {model_name} will not be available.")
    else:
        logger.info(f"Found API key for {model_name}")

# Models for request/response
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    model: str = "gpt-4o-mini"
    thread_id: Optional[str] = None
    file_url: Optional[str] = None
    use_agent: bool = False
    deep_research: bool = False
    stream: bool = False

class ChatResponse(BaseModel):
    message: Message
    thread_id: str

# New streaming response class
class StreamingChatResponse(BaseModel):
    message: Message
    thread_id: str

# Model mapping for direct access with API keys from environment variables
def get_model_clients():
    clients = {}
    
    # OpenAI models - requires OPENAI_API_KEY
    if os.getenv("OPENAI_API_KEY"):
        clients["gpt-4o-mini"] = lambda: ChatOpenAI(
            model="gpt-4o-mini", 
            api_key=os.getenv("OPENAI_API_KEY"),
            streaming=True  # Enable streaming by default
        )
        clients["gpt-4o"] = lambda: ChatOpenAI(
            model="gpt-4o", 
            api_key=os.getenv("OPENAI_API_KEY"),
            streaming=True
        )
    
    # Google models - requires GOOGLE_API_KEY
    if os.getenv("GOOGLE_API_KEY"):
        clients["gemini-1.5-flash"] = lambda: ChatGoogleGenerativeAI(
            model="gemini-1.5-flash", 
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            streaming=True
        )
        clients["gemini-1.5-pro"] = lambda: ChatGoogleGenerativeAI(
            model="gemini-1.5-pro", 
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            streaming=True
        )
    
    # Anthropic models - requires ANTHROPIC_API_KEY
    if os.getenv("ANTHROPIC_API_KEY"):
        clients["claude-3-haiku-20240307"] = lambda: ChatAnthropic(
            model="claude-3-haiku-20240307", 
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            streaming=True
        )
        clients["claude-3-opus-20240229"] = lambda: ChatAnthropic(
            model="claude-3-opus-20240229", 
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            streaming=True
        )
    
    # Groq models - requires GROQ_API_KEY
    if os.getenv("GROQ_API_KEY"):
        # Using Llama 3 models from Groq
        clients["llama-3.3-70b-versatile"] = lambda: ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=os.getenv("GROQ_API_KEY"),
            streaming=True
        )
        # Add another Groq model option
        clients["mixtral-8x7b-32768"] = lambda: ChatGroq(
            model="mixtral-8x7b-32768",
            api_key=os.getenv("GROQ_API_KEY"),
            streaming=True
        )
    
    return clients

# Initialize model clients
MODEL_CLIENTS = get_model_clients()

# If no models are available, log a warning
if not MODEL_CLIENTS:
    logger.error("No model clients could be initialized. Please check your API keys.")

# Check for Tavily API key (needed for search tool)
if not os.getenv("TAVILY_API_KEY"):
    logger.warning("TAVILY_API_KEY not set. React agent search functionality will be limited.")

# New model for the request
class ReactAgentRequest(BaseModel):
    messages: List[Message]
    model: str = "gpt-4o-mini"
    thread_id: Optional[str] = None
    file_url: Optional[str] = None
    max_search_results: int = 3

# Utility functions
def handle_file_upload(file: UploadFile) -> str:
    """Process uploaded file and return the local file path."""
    try:
        timestamp = int(time.time())
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{timestamp}_{uuid.uuid4().hex}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        with open(file_path, "wb") as f:
            f.write(file.file.read())
            
        abs_path = os.path.abspath(file_path)
        logger.info(f"File uploaded: {file.filename} -> {abs_path}")
        return abs_path
    except Exception as e:
        logger.error(f"Error handling file upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {str(e)}")

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file and return its path."""
    file_path = handle_file_upload(file)
    return {"file_path": file_path}

@app.post("/api/chat")
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    """Process a chat message and return the response."""
    try:
        # Create or get thread ID
        thread_id = request.thread_id or str(uuid.uuid4())
        
        # Use request.stream directly instead of model_dump().get()
        stream = request.stream
        
        # Convert frontend messages to LangChain format
        langchain_messages = []
        for msg in request.messages:
            if msg.role == "user":
                # Fix: Ensure content is not empty
                content = msg.content.strip() if msg.content else "Hello"
                langchain_messages.append(HumanMessage(content=content))
            elif msg.role == "assistant":
                # Fix: Ensure content is not empty
                content = msg.content.strip() if msg.content else "I'm an AI assistant."
                langchain_messages.append(AIMessage(content=content))
        
        # Ensure we have at least one message
        if not langchain_messages:
            langchain_messages = [HumanMessage(content="Hello")]
        
        # Log the incoming request with emphasis on the selected model
        logger.info(f"Received chat request: model={request.model}, thread_id={thread_id}, use_agent={request.use_agent}, stream={stream}")
        
        # Get the model name from the current request (allowing model switching)
        backend_model = request.model
        logger.info(f"Using model for this message: {backend_model}")
        
        # If streaming is requested, handle it differently
        if stream:
            # Return a streaming response
            return StreamingResponse(
                stream_chat_response(
                    messages=langchain_messages,
                    model=backend_model,
                    thread_id=thread_id,
                    use_agent=request.use_agent,
                    deep_research=request.deep_research,
                    file_url=request.file_url
                ),
                media_type="text/event-stream"
            )
        
        # Only use agent when requested via the bulb icon (use_agent=True)
        if request.use_agent:
            logger.info(f"Using agent for chat with model: {backend_model}")
            
            # Prepare input state for the agent with proper structure
            input_state = VaaniState(
                messages=langchain_messages,
                file_url=request.file_url,
                indexed=False,
                deep_research_requested=request.deep_research,
                agent_name="web_search_agent" if request.deep_research else None,
                model_name=backend_model,
                reflect_iterations=0,
                reflection_data=None
            )
            
            # Configure agent
            config = {"configurable": {"thread_id": thread_id}}
            
            # Process with agent with better error handling
            logger.info(f"Processing with agent - Thread: {thread_id}, Model: {backend_model}")
            
            try:
                # Wrap the synchronous invoke in asyncio.to_thread for async handling
                result = await asyncio.to_thread(agt_graph.invoke, input_state, config)
                
                # Extract response with proper error checking
                if "messages" in result and result["messages"] and len(result["messages"]) > 0:
                    ai_message = result["messages"][-1]
                    if hasattr(ai_message, "content"):
                        response_content = ai_message.content
                    elif isinstance(ai_message, dict) and "content" in ai_message:
                        response_content = ai_message["content"]
                    else:
                        logger.warning(f"Unexpected message format: {type(ai_message)}")
                        response_content = "I couldn't process your request properly."
                else:
                    logger.warning("No messages found in agent result")
                    response_content = "I couldn't process your request with the AI agent. Please try again."
                
            except Exception as invoke_error:
                logger.error(f"Error in agent processing: {invoke_error}", exc_info=True)
                return ChatResponse(
                    message=Message(role="assistant", content=f"I encountered an error with the AI agent: {str(invoke_error)}"),
                    thread_id=thread_id
                )
        else:
            # Direct model conversation without agent
            logger.info(f"Using direct model conversation - Model: {backend_model}")
            
            # Check if we have a model client for the requested model
            if backend_model not in MODEL_CLIENTS:
                # If no matching model and no API keys, return an error
                if not MODEL_CLIENTS:
                    return ChatResponse(
                        message=Message(role="assistant", content="No API keys are configured. Please add API keys to your .env file."),
                        thread_id=thread_id
                    )
                # Log available models for debugging
                logger.warning(f"Available models: {list(MODEL_CLIENTS.keys())}")
                logger.warning(f"Model {backend_model} not found, using first available model")
                # If the model doesn't exist but we have some clients, use the first available
                backend_model = next(iter(MODEL_CLIENTS.keys()))
                logger.warning(f"Falling back to: {backend_model}")
            
            try:
                # Get the appropriate model client
                model = MODEL_CLIENTS[backend_model]()
                logger.info(f"Using model client for: {backend_model}")
                
                # Get response directly from the model
                ai_response = await asyncio.to_thread(
                    model.invoke, 
                    langchain_messages
                )
                response_content = ai_response.content
            except Exception as model_error:
                logger.error(f"Error in model processing: {model_error}", exc_info=True)
                return ChatResponse(
                    message=Message(role="assistant", content=f"I encountered an error with the {backend_model} model: {str(model_error)}"),
                    thread_id=thread_id
                )
        
        logger.info(f"Generated response using {backend_model} (first 100 chars): {response_content[:100]}...")
        
        return ChatResponse(
            message=Message(role="assistant", content=response_content),
            thread_id=thread_id
        )
    
    except Exception as e:
        logger.error(f"Error processing chat request: {e}", exc_info=True)
        return ChatResponse(
            message=Message(role="assistant", content=f"I encountered an error: {str(e)}"),
            thread_id=thread_id or str(uuid.uuid4())
        )

# Add the streaming function for the chat endpoint
async def stream_chat_response(messages, model, thread_id, use_agent, deep_research, file_url):
    """Generate a streaming response for chat."""
    try:
        # Initial status update with no delay
        yield json.dumps({"type": "status", "status": "Processing..."}) + "\n"
        
        # Model client validation (keep existing code)
        if model not in MODEL_CLIENTS:
            logger.warning(f"Model {model} not found in available models: {list(MODEL_CLIENTS.keys())}")
            if not MODEL_CLIENTS:
                yield json.dumps({
                    "type": "result",
                    "message": {"role": "assistant", "content": "No API keys are configured. Please add API keys to your .env file."},
                    "thread_id": thread_id
                }) + "\n"
                return
            # Log available models for debugging
            logger.warning(f"Available models: {list(MODEL_CLIENTS.keys())}")
            logger.warning(f"Model {model} not found, using first available model")
            # If the model doesn't exist but we have some clients, use the first available
            model = next(iter(MODEL_CLIENTS.keys()))
            logger.warning(f"Falling back to: {model}")
        
        # Get the model client (already configured with streaming=True)
        model_client = MODEL_CLIENTS[model]()
        logger.info(f"Using streaming-enabled model: {model}")
        
        if use_agent:
            # For agent, we need a different approach since agents don't natively stream
            yield json.dumps({"type": "status", "status": "Running agent..."}) + "\n"
            
            # Prepare input state for the agent
            input_state = VaaniState(
                messages=messages,
                file_url=file_url,
                indexed=False,
                deep_research_requested=deep_research,
                agent_name="web_search_agent" if deep_research else None,
                model_name=model,
                reflect_iterations=0,
                reflection_data=None
            )
            
            # Configure agent
            config = {"configurable": {"thread_id": thread_id}}
            
            # Process with agent
            try:
                # Start a background task for the agent processing
                async def run_agent():
                    return await asyncio.to_thread(agt_graph.invoke, input_state, config)
                
                agent_task = asyncio.create_task(run_agent())
                
                # Show progress updates while agent is running
                progress_steps = ["Thinking", "Processing", "Analyzing", "Formulating response"]
                step_index = 0
                
                while not agent_task.done():
                    yield json.dumps({"type": "status", "status": f"{progress_steps[step_index]}..."}) + "\n"
                    step_index = (step_index + 1) % len(progress_steps)
                    try:
                        await asyncio.wait_for(asyncio.shield(agent_task), 1.0)
                    except asyncio.TimeoutError:
                        pass
                
                # Get result from completed task
                result = await agent_task
                
                # Extract response content
                if "messages" in result and result["messages"] and len(result["messages"]) > 0:
                    ai_message = result["messages"][-1]
                    if hasattr(ai_message, "content"):
                        response_content = ai_message.content
                    elif isinstance(ai_message, dict) and "content" in ai_message:
                        response_content = ai_message["content"]
                    else:
                        response_content = "I couldn't process your request properly."
                else:
                    response_content = "I couldn't process your request with the AI agent. Please try again."
                
                # KEY CHANGE: Stream one token/chunk at a time instead of cumulative content
                words = response_content.split(' ')
                buffer = ""
                word_count = 0
                
                for word in words:
                    buffer += word + " "
                    word_count += 1
                    
                    # Send after every few words or punctuation
                    if word_count >= 3 or any(char in word for char in ['.', '!', '?', '\n']):
                        yield json.dumps({
                            "type": "chunk",
                            "chunk": buffer,
                            "thread_id": thread_id
                        }) + "\n"
                        buffer = ""
                        word_count = 0
                        await asyncio.sleep(random.uniform(0.005, 0.015))
                
                # Send final complete message
                yield json.dumps({
                    "type": "result",
                    "message": {"role": "assistant", "content": response_content},
                    "thread_id": thread_id
                }) + "\n"
                
            except Exception as invoke_error:
                logger.error(f"Error in agent processing: {invoke_error}", exc_info=True)
                yield json.dumps({
                    "type": "result",
                    "message": {"role": "assistant", "content": f"I encountered an error with the AI agent: {str(invoke_error)}"},
                    "thread_id": thread_id
                }) + "\n"
        
        else:
            # Direct model conversation with real streaming
            try:
                # Choose streaming method based on available API
                if hasattr(model_client, "astream") and callable(getattr(model_client, "astream")):
                    logger.info(f"Using astream for {model}")
                    stream = model_client.astream(messages)
                    
                    # Process tokens individually instead of accumulating
                    async for chunk in stream:
                        # Extract content based on model response format
                        chunk_content = ""
                        if hasattr(chunk, "content") and chunk.content:
                            chunk_content = chunk.content
                        elif isinstance(chunk, dict) and "content" in chunk:
                            chunk_content = chunk["content"]
                        elif hasattr(chunk, "delta") and hasattr(chunk.delta, "content") and chunk.delta.content:
                            chunk_content = chunk.delta.content
                        
                        if chunk_content:
                            # Send each chunk immediately as it arrives without accumulating
                            yield json.dumps({
                                "type": "chunk",
                                "chunk": chunk_content,
                                "thread_id": thread_id
                            }) + "\n"
                            # MODIFIED: Decreased delay to 0.005-0.015s (2x faster)
                            await asyncio.sleep(random.uniform(0.005, 0.015))
                    
                    # Send final result signal (frontend will have accumulated the full message)
                    yield json.dumps({
                        "type": "done",
                        "thread_id": thread_id
                    }) + "\n"
                    return
                
            except Exception as model_error:
                logger.error(f"Error in model processing: {model_error}", exc_info=True)
                yield json.dumps({
                    "type": "result",
                    "message": {"role": "assistant", "content": f"I encountered an error with the {model} model: {str(model_error)}"},
                    "thread_id": thread_id
                }) + "\n"
    
    except Exception as e:
        logger.error(f"Error in streaming chat response: {e}", exc_info=True)
        yield json.dumps({
            "type": "result",
            "message": {"role": "assistant", "content": f"I encountered an error: {str(e)}"},
            "thread_id": thread_id
        }) + "\n"

@app.get("/api/models")
async def get_available_models():
    """Return a list of available models based on configured API keys."""
    available_models = list(MODEL_CLIENTS.keys())
    return {"models": available_models}

@app.post("/api/react-search")
async def react_agent_search(request: ReactAgentRequest):
    """Process a chat message using the ReAct agent with search capabilities."""
    try:
        # Create or get thread ID
        thread_id = request.thread_id or str(uuid.uuid4())
        
        # Convert frontend messages to LangChain format
        langchain_messages = []
        for msg in request.messages:
            if msg.role == "user":
                langchain_messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                langchain_messages.append(AIMessage(content=msg.content))
        
        # Log the request
        logger.info(f"Received react-agent request: model={request.model}, thread_id={thread_id}")
        
        # Prepare input state for the ReAct agent - without memory_config
        input_state = InputState(
            messages=langchain_messages
            # Don't include memory_config parameter as it doesn't exist
        )
        
        # Map the model names to the format expected by react_agent with correct handling
        model_mapping = {
            "gemini-1.5-flash": "google/gemini-1.5-flash",
            "gpt-4o-mini": "openai/gpt-4o-mini",
            "gpt-4o": "openai/gpt-4o",  # Fixed duplicate with unique value
            "claude-3-haiku-20240307": "anthropic/claude-3-haiku-20240307",
            "llama-3.3-70b-versatile": "groq/llama-3.3-70b-versatile",
            "mixtral-8x7b-32768": "groq/mixtral-8x7b-32768"
        }
        
        # Use mapped model name with fallback handling
        agent_model = model_mapping.get(request.model)
        if not agent_model:
            # If model not found in mapping, use default format
            logger.warning(f"Model {request.model} not found in mapping, using default format")
            agent_model = f"default/{request.model}"
        
        # Create configuration
        config = Configuration(
            model=agent_model,
            max_search_results=request.max_search_results
        )
        
        # Add fallback model logic (this is still good to keep)
        available_models = list(MODEL_CLIENTS.keys())
        fallback_models = []
        
        for model_id in ["gpt-4o-mini", "gemini-1.5-flash", "llama-3.3-70b-versatile"]:
            mapped_name = model_mapping.get(model_id)
            if model_id in available_models and mapped_name != agent_model:
                fallback_models.append(mapped_name)
        
        if fallback_models:
            config.fallback_model = fallback_models[0]
            logger.info(f"Setting fallback model to: {fallback_models[0]}")
        
        # Invoke the ReAct agent ASYNCHRONOUSLY
        try:
            logger.info(f"Processing with react-agent: {agent_model}, max_results={request.max_search_results}")
            
            # Use the async API directly instead of asyncio.to_thread
            result = await react_graph.ainvoke(
                input_state, 
                {"configurable": {"thread_id": thread_id, "configuration": config}}
            )
            
            # Extract the assistant's response - FIX: Check the correct structure
            # The result is an AddableValuesDict and messages are accessed differently
            if "messages" in result and result["messages"]:
                ai_message = result["messages"][-1]
                response_content = ai_message.content
                
                # Add source citation formatting to ensure search results are properly cited
                search_tool_outputs = [msg for msg in result["messages"] if hasattr(msg, "tool_calls") and msg.tool_calls]
                
                if search_tool_outputs:
                    sources = []
                    for msg in search_tool_outputs:
                        for tool_call in msg.tool_calls:
                            if tool_call.get("name") == "search" and tool_call.get("output"):
                                tool_output = tool_call.get("output")
                                if isinstance(tool_output, list):
                                    for item in tool_output:
                                        if item.get("url") and item.get("title"):
                                            sources.append({
                                                'url': item.get('url'),
                                                'title': item.get('title')
                                            })
                    
                    if sources:
                        response_content += format_source_urls(sources)
            else:
                logger.warning("No messages found in react-agent result")
                response_content = "I couldn't find any useful information. Please try a different query."
                
        except anthropic.InternalServerError as anthropic_error:
            # Handle Claude API overloaded errors specifically
            logger.error(f"Claude API error: {anthropic_error}")
            return ChatResponse(
                message=Message(role="assistant", content="Sorry, the AI service is currently experiencing high load. Please try again in a few moments or switch to a different model."),
                thread_id=thread_id
            )
        except Exception as agent_error:
            logger.error(f"Error in react-agent processing: {agent_error}", exc_info=True)
            return ChatResponse(
                message=Message(role="assistant", content=f"I encountered an error with the research agent: {str(agent_error)}"),
                thread_id=thread_id
            )
            
        # Return the response
        logger.info(f"Generated react-agent response (first 100 chars): {response_content[:100]}...")
        
        return ChatResponse(
            message=Message(role="assistant", content=response_content),
            thread_id=thread_id
        )
        
    except Exception as e:
        logger.error(f"Error processing react-agent request: {e}", exc_info=True)
        return ChatResponse(
            message=Message(role="assistant", content=f"I encountered an error with the research agent: {str(e)}"),
            thread_id=thread_id or str(uuid.uuid4())
        )

@app.post("/api/react-search-streaming")
async def react_agent_search_streaming(request: ReactAgentRequest):
    """Process a chat message using the ReAct agent with search capabilities and stream status updates."""
    
    async def event_generator():
        """Generate server-sent events with status updates."""
        try:
            # Initial status
            yield json.dumps({"type": "status", "status": "Starting research..."}) + "\n"
            
            # Convert frontend messages to LangChain format
            langchain_messages = []
            for msg in request.messages:
                if msg.role == "user":
                    langchain_messages.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    langchain_messages.append(AIMessage(content=msg.content))
            
            # Create or get thread ID
            thread_id = request.thread_id or str(uuid.uuid4())
            
            # Prepare input state for the ReAct agent
            input_state = InputState(messages=langchain_messages)
            
            # Map the model names to the format expected by react_agent
            model_mapping = {
                "gemini-1.5-flash": "google/gemini-1.5-flash",
                "gpt-4o-mini": "openai/gpt-4o-mini",
                "gpt-4o": "openai/gpt-4o",  # Fixed duplicate with unique value
                "claude-3-haiku-20240307": "anthropic/claude-3-haiku-20240307",
                "llama-3.3-70b-versatile": "groq/llama-3.3-70b-versatile",
                "mixtral-8x7b-32768": "groq/mixtral-8x7b-32768"
            }
            
            # Use mapped model name or fallback
            agent_model = model_mapping.get(request.model, f"default/{request.model}")
            
            # Create configuration
            config = Configuration(
                model=agent_model,
                max_search_results=request.max_search_results
            )
            
            # Add callback for status updates
            class StatusCallback:
                async def on_agent_action(self, action_type, description):
                    status_update = {"type": "status", "status": f"{action_type}: {description}"}
                    yield json.dumps(status_update) + "\n"
                    await asyncio.sleep(0.1)
            
            status_callback = StatusCallback()
            
            # Update config with callback
            config_dict = {"configurable": {
                "thread_id": thread_id, 
                "configuration": config,
                "callback": status_callback
            }}
            
            # Create a task to run the react_graph.ainvoke call
            async def run_react_agent():
                return await react_graph.ainvoke(input_state, config_dict)
            
            task = asyncio.create_task(run_react_agent())
            
            # Send real-time status updates while the task runs
            yield json.dumps({"type": "status", "status": "Researching..."}) + "\n"
            
            # Then rely on actual status updates from the agent instead of fake ones
            while not task.done():
                try:
                    # Wait a short time before checking again
                    await asyncio.wait_for(asyncio.shield(task), 0.5)
                except asyncio.TimeoutError:
                    # Task not done yet, continue
                    pass
            
            # Get result from completed task
            result = await task
            
            # Extract the assistant's response (keep existing code)
            if "messages" in result and result["messages"]:
                ai_message = result["messages"][-1]
                response_content = ai_message.content
                
                # Add source citation formatting to ensure search results are properly cited
                search_tool_outputs = [msg for msg in result["messages"] if hasattr(msg, "tool_calls") and msg.tool_calls]
                
                if search_tool_outputs:
                    sources = []
                    for msg in search_tool_outputs:
                        for tool_call in msg.tool_calls:
                            if tool_call.get("name") == "search" and tool_call.get("output"):
                                tool_output = tool_call.get("output")
                                if isinstance(tool_output, list):
                                    for item in tool_output:
                                        if item.get("url") and item.get("title"):
                                            sources.append({
                                                'url': item.get('url'),
                                                'title': item.get('title')
                                            })
                    
                    if sources:
                        response_content += format_source_urls(sources)
            else:
                logger.warning("No messages found in react-agent result")
                response_content = "I couldn't find any useful information. Please try a different query."
            
            # Stream the response back word-by-word for a fluid experience
            words = response_content.split(' ')
            buffer = ""
            word_count = 0
            
            for word in words:
                buffer += word + " "
                word_count += 1
                
                # Send after every few words or punctuation
                if word_count >= 3 or any(char in word for char in ['.', '!', '?', '\n']):
                    yield json.dumps({
                        "type": "chunk",
                        "chunk": buffer,
                        "thread_id": thread_id
                    }) + "\n"
                    buffer = ""
                    word_count = 0
                    await asyncio.sleep(random.uniform(0.005, 0.015))
            
            # Send any remaining buffer
            if buffer:
                yield json.dumps({
                    "type": "chunk",
                    "chunk": buffer,
                    "thread_id": thread_id
                }) + "\n"
                # Small delay before final result (also doubled)
                await asyncio.sleep(0.025)
            
            # Send final result
            yield json.dumps({
                "type": "result",
                "message": {"role": "assistant", "content": response_content},
                "thread_id": thread_id
            }) + "\n"
                
        except Exception as e:
            logger.error(f"Error in streaming: {e}", exc_info=True)
            error_result = {
                "type": "result",
                "message": {"role": "assistant", "content": f"I encountered an error: {str(e)}"},
                "thread_id": thread_id or str(uuid.uuid4())
            }
            yield json.dumps(error_result) + "\n"
    
    # Return a streaming response
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

# Update health check endpoint
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "environment": "production"
    }

# Add this helper function to format source URLs with titles
def format_source_urls(sources):
    if not sources:
        return ""
    
    formatted_sources = []
    for source in sources:
        title = source.get('title', 'Source')
        url = source.get('url', '')
        if url:
            # Clean and truncate the title if needed
            title = title.strip()
            if len(title) > 60:
                title = title[:57] + "..."
            formatted_sources.append(f"[{title}]({url})")
    
    if formatted_sources:
        return "\n\n**Sources:**\n" + "\n".join(formatted_sources)
    return ""

# Add this function to instruct the AI about media generation
def handle_media_generation(prompt, media_type="image"):
    """Instructs the AI to properly format media generation results."""
    if media_type == "image":
        # For image generation, instruct the AI to include proper image markdown
        return f"For image generation of '{prompt}', please include image URLs in markdown format: ![Generated Image](URL)"
    elif media_type == "music":
        # For music generation, instruct the AI about audio URLs
        return f"For music generation of '{prompt}', please include audio URLs directly, preferably as mp3 links."

@app.options("/{full_path:path}")
async def options_handler(full_path: str):
    return {}  # This will return 200 OK with CORS headers applied by middleware

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 