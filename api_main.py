from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()

import os
import uvicorn

for _var in ("ADMIN_API_KEY", "USER_API_KEY"):
    if not os.environ.get(_var):
        raise RuntimeError(f"Required env var {_var} is not set")

from src.api import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, log_config=None)
