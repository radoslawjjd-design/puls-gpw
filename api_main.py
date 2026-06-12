from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()

import uvicorn

from src.api import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, log_config=None)
