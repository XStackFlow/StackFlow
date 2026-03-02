import os
from dotenv import load_dotenv
from langfuse import Langfuse
import time

def test_manual_push():
    load_dotenv()
    
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST")
    
    print(f"Testing with Host: {host}")
    print(f"Public Key: {public_key[:8]}...")
    
    # In v3, you can use the Langfuse client for manual tracing if needed,
    # but the context manager style is preferred.
    langfuse = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
        debug=True
    )
    
    print("Starting trace using v3 context manager...")
    with langfuse.start_as_current_span(name="Manual Test Span V3") as span:
        span.update(
            input="test input v3",
            output="Manual test successful v3",
            metadata={"sdk_version": "v3"}
        )
        
        # Nested generation example
        with langfuse.start_as_current_generation(name="Manual Test Generation V3") as generation:
            generation.update(
                model="gpt-3.5-turbo",
                input="hello world",
                output="hello from v3"
            )
            time.sleep(0.5)
            
        time.sleep(0.5)
        
    print("Flushing...")
    langfuse.flush()
    print("Flush complete. Please check Langfuse dashboard.")

if __name__ == "__main__":
    test_manual_push()
