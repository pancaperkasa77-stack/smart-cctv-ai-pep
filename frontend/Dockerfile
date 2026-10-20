# Smart CCTV AI — .NET 4.8 (MVC5) visual mockup, hosted via Mono + xsp4.
# Multi-stage: build stage compiles the MVC5 project with xbuild/nuget,
# runtime stage only carries mono-xsp4 + the compiled webroot.
#
# Base image is debian:buster — EOL, so apt must point at archive.debian.org
# (see the shared fix baked into both stages below); this was validated
# end-to-end in /tmp/mvc5-smoke before being carried into this Dockerfile.

FROM debian:buster AS build

RUN set -eux; \
    printf 'deb http://archive.debian.org/debian buster main contrib non-free\ndeb http://archive.debian.org/debian buster-updates main contrib non-free\ndeb http://archive.debian.org/debian-security buster/updates main contrib non-free\n' > /etc/apt/sources.list; \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99no-check-valid; \
    printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4; \
    apt-get update -qq; \
    apt-get install -y -qq mono-devel nuget ca-certificates-mono; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .

RUN set -eux; \
    rm -rf bin obj packages webroot; \
    nuget restore packages.config -PackagesDirectory packages -Source https://www.nuget.org/api/v2/; \
    xbuild /p:Configuration=Release /t:Rebuild MvcSmartCctv.csproj; \
    mkdir -p webroot/bin; \
    cp bin/*.dll webroot/bin/; \
    cp packages/Microsoft.AspNet.Mvc.5.2.9/lib/net45/System.Web.Mvc.dll webroot/bin/; \
    cp packages/Microsoft.AspNet.Razor.3.2.9/lib/net45/System.Web.Razor.dll webroot/bin/; \
    cp packages/Microsoft.AspNet.WebPages.3.2.9/lib/net45/System.Web.WebPages.dll webroot/bin/; \
    cp packages/Microsoft.AspNet.WebPages.3.2.9/lib/net45/System.Web.WebPages.Deployment.dll webroot/bin/; \
    cp packages/Microsoft.AspNet.WebPages.3.2.9/lib/net45/System.Web.WebPages.Razor.dll webroot/bin/; \
    cp packages/Microsoft.AspNet.WebPages.3.2.9/lib/net45/System.Web.Helpers.dll webroot/bin/; \
    cp packages/Microsoft.Web.Infrastructure.2.0.0/lib/net40/Microsoft.Web.Infrastructure.dll webroot/bin/; \
    cp Web.config webroot/; \
    cp Global.asax webroot/; \
    cp -r Views webroot/; \
    cp -r Content webroot/


FROM debian:buster AS runtime

RUN set -eux; \
    printf 'deb http://archive.debian.org/debian buster main contrib non-free\ndeb http://archive.debian.org/debian buster-updates main contrib non-free\ndeb http://archive.debian.org/debian-security buster/updates main contrib non-free\n' > /etc/apt/sources.list; \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99no-check-valid; \
    printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4; \
    apt-get update -qq; \
    apt-get install -y -qq mono-xsp4; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /src/webroot ./

EXPOSE 8080

# xsp4 blocks on stdin waiting for Enter to stop; under `docker run` with no
# TTY, stdin gets immediate EOF (treated as "Enter pressed") and it exits
# right away. `tail -f /dev/null` feeds it a pipe that never closes —
# confirmed necessary in the smoke test.
CMD ["/bin/sh", "-c", "tail -f /dev/null | xsp4 --port 8080 --address 0.0.0.0"]
