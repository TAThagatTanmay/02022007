FROM python:3.11-slim

# Install system dependencies needed for building native extensions like dlib and OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    libboost-python-dev \
    python3-dev \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy your app code into the container
COPY . /app

# Upgrade pip and install python dependencies from requirements.txt
RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port your Flask app runs on
EXPOSE 5000

# Command to run your application (adjust as necessary)
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000"]
