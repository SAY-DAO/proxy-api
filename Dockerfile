# Dockerfile
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements.txt file into the container
COPY requirements.txt .

# Install the required dependencies
RUN pip install -r requirements.txt

# Copy the Flask app code into the container
COPY app.py .

# Expose the port the app will run on
EXPOSE 5000

# Use Gunicorn for production
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:5000"]
