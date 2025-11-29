from fastapi.middleware.cors import CORSMiddleware
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse

from .core import run_batch  # <- your existing batch logic

app = FastAPI(title="JPG -> Base64 SaaS", version="0.1.0")

# Allow frontend (any origin) to call this API during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # later you can lock this down to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    # Only JPG/JPEG files, to match your script
    for f in files:
        if not f.filename.lower().endswith((".jpg", ".jpeg")):
            raise HTTPException(
                status_code=400,
                detail=f"Only JPG/JPEG files allowed. Invalid: {f.filename}",
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

        # IMPORTANT:
        # Do NOT delete base_tmp here; the file needs to exist
        # while FileResponse is streaming it to the client.
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
