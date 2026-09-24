# The documentation site as a container: build the HTML from the Markdown, then
# serve it with nginx.
#
#   docker build -f docs/Dockerfile -t sel-docs .
#   docker run --rm -p 8080:80 sel-docs          # http://localhost:8080/
#
# The build context is the repository root: the pages link to examples, specs
# and tools, and the build checks that every one of those links resolves, so it
# needs the tree they point into (.dockerignore keeps the build output out).

FROM node:24-alpine AS build
WORKDIR /src
COPY package.json ./
RUN npm install --no-audit --no-fund --ignore-scripts --no-package-lock \
      marked@16 highlight.js@11 github-slugger@2 >/dev/null
COPY . .
RUN node tools/build-docs.mjs --out /site

FROM nginx:1-alpine
COPY --from=build /site /usr/share/nginx/html
RUN printf '%s\n' \
      'server {' \
      '  listen 80;' \
      '  root /usr/share/nginx/html;' \
      '  index index.html;' \
      '  error_page 404 /404.html;' \
      '  location / { try_files $uri $uri/ =404; }' \
      '  location /docs/assets/ { expires 1h; }' \
      '}' > /etc/nginx/conf.d/default.conf
EXPOSE 80
