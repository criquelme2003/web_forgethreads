# Etapa 1: build del frontend (Vite + React -> app/front/dist)
FROM node:22-slim AS front-build

WORKDIR /front

COPY ./app/front/package.json ./app/front/package-lock.json ./

RUN npm ci --no-audit --no-fund

COPY ./app/front ./

RUN npm run build

# Etapa 2: backend FastAPI (sirve app/front/dist en /front/*)
FROM python:3.14

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY ./app /code/app

COPY --from=front-build /front/dist /code/app/front/dist

CMD ["fastapi", "run", "app/main.py","--proxy-headers", "--port", "80"]
