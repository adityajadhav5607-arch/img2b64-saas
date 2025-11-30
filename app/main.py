from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
import tempfile
import uuid
from pathlib import Path
import os

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.responses import FileResponse

from .core import run_batch  # <- your existing batch logic

import stripe
from dotenv import load_dotenv

# Load environment variables from .env (locally)
load_dotenv()

# Stripe + config from env
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
MONTHLY_PRICE_ID = os.getenv("MONTHLY_PRICE_ID")
LIFETIME_PRICE_ID = os.getenv("LIFETIME_PRICE_ID")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")

app = FastAPI(title="JPG -> Base64 SaaS", version="0.1.0")

# Allow frontend (any origin) to call this API during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # later you can lock this down to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional simple health route so "/" isn't 404
@app.get("/")
def read_root():
    return {"status": "ok", "message": "JPG -> Base64 SaaS API"}


# --- TEMP AUTH PLACEHOLDER (everyone allowed for now) ---
def get_current_user():
    # Later, we'll check a cookie / token tied to Stripe.
    return {"email": "dev@example.com", "plan": "dev"}


@app.post("/api/convert")
async def convert_images(
    files: list[UploadFile] = File(...),
    user=Depends(get_current_user),
):
    # Ensure user exists (later enforce paid-only)
    if user is None:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Basic validation
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # Allow JPG/JPEG/PNG files
    for f in files:
        if not f.filename.lower().endswith((".jpg", ".jpeg", ".png")):
            raise HTTPException(
                status_code=400,
                detail=f"Only JPG/JPEG/PNG files allowed. Invalid: {f.filename}",
            )

    # Temp working directory
    work_id = str(uuid.uuid4())
    base_tmp = Path(tempfile.gettempdir()) / f"img2b64_{work_id}"
    input_dir = base_tmp / "input"
    output_dir = base_tmp / "output"

    try:
        # Create temp dirs
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save uploaded files
        for f in files:
            dest = input_dir / f.filename
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)

        # Call your existing batch logic
        exit_code = run_batch(
            input_dir=input_dir,
            output_dir=output_dir,
            recurse=False,       # we don't need subfolders for uploads
            data_uri=False,      # set True if you want data: prefix
            cap_chars=None,      # no cap by default
            csv_map=True,        # also create manifest.csv
            max_px=800,
            quality_floor=50,
            log=print,           # print to server console for debugging
        )

        if exit_code != 0:
            # Something went wrong inside run_batch
            # Clean up and error
            shutil.rmtree(base_tmp, ignore_errors=True)
            raise HTTPException(status_code=500, detail="Conversion failed")

        # Zip output_dir
        zip_base = base_tmp / "result"
        shutil.make_archive(str(zip_base), "zip", output_dir)
        zip_path = zip_base.with_suffix(".zip")

        # NOTE: we are not deleting base_tmp here so the file exists
        # while FileResponse streams it to the client.
        return FileResponse(
            path=str(zip_path),
            filename="converted_base64.zip",
            media_type="application/zip",
        )

    except HTTPException:
        # Raise FastAPI-friendly errors unchanged
        raise
    except Exception as e:
        # Any unexpected error: cleanup and 500
        print("Unexpected error in /api/convert:", e)
        shutil.rmtree(base_tmp, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

class CheckoutRequest(BaseModel):
    plan: str
# --- STRIPE: create checkout session endpoint ---
@app.post("/create-checkout-session")
async def create_checkout_session(body: CheckoutRequest):
    plan = body.plan

    if plan not in ("monthly", "lifetime"):
        raise HTTPException(status_code=400, detail="Invalid plan")

    if plan == "monthly":
        if not MONTHLY_PRICE_ID:
            raise HTTPException(status_code=500, detail="Monthly price not configured")
        price_id = MONTHLY_PRICE_ID
        mode = "subscription"
    else:
        if not LIFETIME_PRICE_ID:
            raise HTTPException(status_code=500, detail="Lifetime price not configured")
        price_id = LIFETIME_PRICE_ID
        mode = "payment"

    try:
        session = stripe.checkout.Session.create(
            mode=mode,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{APP_BASE_URL}/success.html?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{APP_BASE_URL}/cancelled.html",
        )
        return {"url": session.url}
    except Exception as e:
        print("Stripe error:", e)
        raise HTTPException(status_code=500, detail="Could not create Stripe Checkout Session")

