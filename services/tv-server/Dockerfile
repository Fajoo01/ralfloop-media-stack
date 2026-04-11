# Usa Python 3.10 leggero
FROM python:3.10-slim

# Installa FFmpeg e dipendenze di sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Imposta la cartella di lavoro
WORKDIR /app

# Copia i requisiti e installali
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- PUNTO CRITICO: COPIA TUTTI I FILE (main.py, resolver.py, ecc) ---
COPY . .

# Avvia il server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]