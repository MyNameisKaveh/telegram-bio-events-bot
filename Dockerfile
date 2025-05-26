# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
# This assumes your requirements.txt is in the root of your repository
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container at /app
# This copies all files from the root of your repository to /app in the container
COPY . .

# Command to run your application when the container launches
# This assumes your main Python file is named app.py
CMD ["python", "app.py"]
