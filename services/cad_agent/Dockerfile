FROM node:22-alpine AS build

WORKDIR /app/services/cad_agent
COPY services/cad_agent/package.json services/cad_agent/package-lock.json ./
RUN npm ci
COPY services/cad_agent/ ./
RUN npm run build

FROM node:22-alpine
WORKDIR /app/services/cad_agent
ENV NODE_ENV=production
COPY services/cad_agent/package.json services/cad_agent/package-lock.json ./
RUN npm ci --omit=dev
COPY --from=build /app/services/cad_agent/dist ./dist
COPY --from=build /app/services/cad_agent/prompts ./prompts
CMD ["node", "dist/main.js"]
