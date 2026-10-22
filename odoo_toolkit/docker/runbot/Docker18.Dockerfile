# FROM ubuntu:noble (core)
FROM ubuntu:noble
ARG PG_VERSION=16

# ENV LANG C.UTF-8
ENV LANG C.UTF-8

# USER root
USER root

# Install debian packages
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends apt-transport-https build-essential ca-certificates curl faketime file fonts-freefont-ttf fonts-noto-cjk gawk gnupg gsfonts libldap2-dev libjpeg9-dev libsasl2-dev libxslt1-dev lsb-release npm ocrmypdf sed sudo unzip xfonts-75dpi zip zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Setup postgresql server

ENV PG_VERSION=${PG_VERSION}
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends postgresql-${PG_VERSION} postgresql-${PG_VERSION}-pgvector locales\
    && rm -rf /var/lib/apt/lists/*
RUN sed -i 's/^# *en_US.UTF-8 UTF-8$/en_US.UTF-8 UTF-8/' /etc/locale.gen && locale-gen


# Install debian packages
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends publicsuffix python3 flake8 python3-dbfread python3-dev python3-gevent python3-pip python3-setuptools python3-wheel python3-markdown python3-mock python3-phonenumbers python3-websocket python3-google-auth libpq-dev pylint python3-jwt python3-asn1crypto python3-html2text python3-suds python3-xmlsec python3-markdown2 python3-aiosmtpd python3-paramiko \
    && rm -rf /var/lib/apt/lists/*

# Install wkhtmltopdf with values {"wkhtmltopdf_version": "0.12.6.1-2", "wkhtmltopdf_os_release": "jammy"}
RUN curl -sSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb -o /tmp/wkhtml.deb \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y install --no-install-recommends --fix-missing -qq /tmp/wkhtml.deb \
    && rm -rf /var/lib/apt/lists/* \
    && rm /tmp/wkhtml.deb

# ENV NODE_PATH=/usr/lib/node_modules/
ENV NODE_PATH=/usr/lib/node_modules/

# ENV npm_config_prefix=/usr
ENV npm_config_prefix=/usr

# RUN npm install
RUN npm install --force -g rtlcss@3.4.0 es-check@6.0.0 eslint@8.1.0 prettier@2.7.1 eslint-config-prettier@8.5.0 eslint-plugin-prettier@4.2.1

# Install branch debian/control with latest postgresql-client with values {"odoo_branch": "18.0", "os_release_name": "`lsb_release -s -c`"}
# This layer updates the repository list to get the latest postgresql-client, mainly needed if the host postgresql version is higher than the default version of the docker os
# CACHE 60
ADD https://raw.githubusercontent.com/odoo/odoo/18.0/debian/control /tmp/control.txt
RUN curl -sSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /etc/apt/trusted.gpg.d/psql_client.asc \
    && echo "deb http://apt.postgresql.org/pub/repos/apt/ `lsb_release -s -c`-pgdg main" > /etc/apt/sources.list.d/pgclient.list \
    && apt-get update \
    && sed -n '/^Depends:/,/^[A-Z]/p' /tmp/control.txt \
        | awk '/^ [a-z]/ { gsub(/,/,"") ; gsub(" ", "") ; print $NF }' | sort -u \
        | DEBIAN_FRONTEND=noninteractive xargs apt-get install -y -qq --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install chrome with values {"chrome_version": "150.0.7871.114-1"}
RUN curl -sSL https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_150.0.7871.114-1_amd64.deb -o /tmp/chrome.deb \
    && apt-get update \
    && apt-get -y install --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb

# Install Odoo Phonenumbers noble with values {"phonenumbers_version": "8.12.57-4+odoo2_all"}
# CACHE 86400
ADD https://packages.odoo.com/pub/python3-phonenumbers_8.12.57-4+odoo2_all.deb /phonenumbers.deb
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends /phonenumbers.deb \
    && rm -rf /var/lib/apt/lists/*

# CACHE 86400
ADD https://github.com/maxmind/MaxMind-DB/raw/main/test-data/GeoIP2-City-Test.mmdb /usr/share/GeoIP/GeoLite2-City.mmdb
# CACHE 86400
ADD https://github.com/maxmind/MaxMind-DB/raw/main/test-data/GeoIP2-Country-Test.mmdb /usr/share/GeoIP/GeoLite2-Country.mmdb

# Install debian packages
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*

# enable venv. Note: cannot use . /venv/main/bin/activate inside docker
RUN python3 -m venv /venv/main --system-site-packages
ENV PATH="/venv/main/bin:$PATH"

# Install pip packages
RUN python3 -m pip install --no-cache-dir ebaysdk==2.1.5 pdf417gen==0.7.1 astroid==3.3.9 pylint==3.3.6

# Install branch requirements no user with values {"odoo_branch": "18.0"}
# CACHE 60
ADD https://raw.githubusercontent.com/odoo/odoo/18.0/requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

# Create user template
RUN groupadd -g ${USERGID} ${USERNAME} && useradd --create-home -u ${USERUID} -g ${USERNAME} -G audio,video ${USERNAME}

# Chmod virtualenv
RUN chmod 777 -R /venv/main

# ENV PYTHONHASHSEED
ENV PYTHONHASHSEED=0

# Faketime at commit with values {"faketime_commit": "3062fb20041552727bdb810456d2f1b4001b341f"}
USER root
RUN set -x ; \
    dpkg-query -l faketime && DEBIAN_FRONTEND=noninteractive apt-get purge -y --no-install-recommends faketime libfaketime || true \
    && curl -sSL https://github.com/wolfcw/libfaketime/archive/3062fb20041552727bdb810456d2f1b4001b341f.tar.gz -o /tmp/faketime-sources.tar.gz \
    && cd /tmp \
    && tar xvf faketime-sources.tar.gz \
    && cd libfaketime-3062fb20041552727bdb810456d2f1b4001b341f \
    && make \
    && make install \
    && rm /tmp/faketime-sources.tar.gz \
    && rm -rf /tmp/libfaketime-3062fb20041552727bdb810456d2f1b4001b341f

# Switch user for [Docker Master Noble]
USER ${USERNAME}
