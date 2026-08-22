# syntax=docker/dockerfile:1
FROM node:22-slim AS base

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_* vars are inlined into the client JS bundle at build time,
# not read at container start — must be passed as build args so a
# production build actually points the browser at the real API/storage
# domains rather than whatever was set (or unset) in the builder's shell.
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_STORAGE_PUBLIC_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_STORAGE_PUBLIC_URL=$NEXT_PUBLIC_STORAGE_PUBLIC_URL

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
