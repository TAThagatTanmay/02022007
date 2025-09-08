# Use official Python 3.11 slim base image
FROM python:3.11-slim

# Install system packages required for dlib and other dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    libboost-python-dev \
    python3-dev \
 && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy all project files to container
COPY . /app

# Upgrade pip, setuptools and wheel for best compatibility
RUN pip install --upgrade pip setuptools wheel

# Install Python dependencies from requirements file
RUN pip install -r requirements.txt

# Expose port your app runs on (Flask default 5000)
EXPOSE 5000

# Start Flask app using gunicorn
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000"]
