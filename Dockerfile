FROM python:3.11-slim

# Install system dependencies for dlib and other packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libopenblas-dev liblapack-dev libx11-dev libgtk-3-dev libboost-python-dev python3-dev \
 && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy project files
COPY . /app

# Upgrade pip and install Python dependencies
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt

# Port on which the app runs (adjust if needed)
EXPOSE 5000

# Command to start the app
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000"]

