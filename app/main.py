from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db, engine, Base
from app import models, auth, crud, schemas, tasks
from app.config import settings
from app.dependencies import get_current_user, get_current_user_optional
import shutil
import os
import uuid

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.PROJECT_NAME)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

import os
from fastapi.templating import Jinja2Templates

# Obtén el directorio donde se encuentra main.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Construye la ruta a la carpeta 'templates'
templates_dir = os.path.join(BASE_DIR, "templates")

# Verifica que la carpeta exista (opcional pero útil para depurar)
if not os.path.exists(templates_dir):
    raise Exception(f"La carpeta de templates no existe en: {templates_dir}")

templates = Jinja2Templates(directory=templates_dir)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_optional)
):
    # Guardar archivos temporalmente
    task = crud.create_task(db, user_id=current_user.id if current_user else None, original_filename=",".join([f.filename for f in files]))
    saved_paths = []
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    for file in files:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Formato no permitido: {ext}")
        safe_name = f"{task.id}_{uuid.uuid4().hex}_{file.filename}"
        file_path = os.path.join(settings.UPLOAD_DIR, safe_name)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_paths.append(file_path)
    
    # Lanzar tarea Celery
    tasks.process_pdfs_task.delay(task.id, saved_paths)
    
    return {"task_id": task.id, "status": "pending"}

@app.get("/task/{task_id}")
async def get_task_status(task_id: int, db: Session = Depends(get_db)):
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(404, "Tarea no encontrada")
    return {"status": task.status, "output_excel": task.output_excel, "output_log": task.output_log, "error": task.error_message}

@app.get("/download/{task_id}/{file_type}")
async def download_file(task_id: int, file_type: str, db: Session = Depends(get_db), current_user = Depends(get_current_user_optional)):
    task = crud.get_task(db, task_id)
    if not task or (task.user_id and current_user and task.user_id != current_user.id):
        raise HTTPException(404, "No autorizado o tarea no existe")
    if file_type == "excel":
        if not task.output_excel:
            raise HTTPException(400, "Archivo no disponible")
        path = os.path.join(settings.OUTPUT_DIR, task.output_excel)
        return FileResponse(path, filename=task.output_excel)
    elif file_type == "log":
        if not task.output_log:
            raise HTTPException(400, "Log no disponible")
        path = os.path.join(settings.OUTPUT_DIR, task.output_log)
        return FileResponse(path, filename=task.output_log)
    else:
        raise HTTPException(400, "Tipo no válido")