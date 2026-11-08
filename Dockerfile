FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libtk8.6 libx11-6 fonts-dejavu-core fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pypdf pymupdf
WORKDIR /app
COPY pdf_multi_tool.py .
ENV HOME=/home/user
WORKDIR /home/user
CMD ["python", "/app/pdf_multi_tool.py"]
