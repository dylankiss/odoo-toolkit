# Use Ubuntu 24.04 LTS (Noble Numbat)
FROM ubuntu:noble

ENV LANG=C.UTF-8 \
    TERM=xterm-256color \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    NODE_PATH=/usr/lib/node_modules/ \
    npm_config_prefix=/usr \
    PATH="/venv/odoo/bin:/venv/odoo-doc/bin:/home/odoo/.local/bin:$PATH"

# Add GeoIP databases
ADD https://github.com/maxmind/MaxMind-DB/raw/main/test-data/GeoIP2-City-Test.mmdb /usr/share/GeoIP/GeoLite2-City.mmdb
ADD https://github.com/maxmind/MaxMind-DB/raw/main/test-data/GeoIP2-Country-Test.mmdb /usr/share/GeoIP/GeoLite2-Country.mmdb

# Perform the following commands as root
USER root

# Install Debian packages
RUN set -x; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        # Runbot packages
        apt-transport-https \
        build-essential \
        ca-certificates \
        curl \
        faketime \
        file \
        fonts-freefont-ttf \
        fonts-noto-cjk \
        gawk \
        gnupg \
        gsfonts \
        libldap2-dev \
        libjpeg9-dev \
        libsasl2-dev \
        libxslt1-dev \
        lsb-release \
        npm \
        ocrmypdf \
        sed \
        sudo \
        unzip \
        xfonts-75dpi \
        zip \
        zlib1g-dev \
        # Extra packages
        git \
        nano \
        openssh-client \
        socat \
        tini \
        vim \
    && rm -rf /var/lib/apt/lists/*

# Install Debian packages
RUN set -x; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        # Runbot packages
        publicsuffix \
        python3 \
        flake8 \
        python3-dbfread \
        python3-dev \
        python3-gevent \
        python3-pip \
        python3-setuptools \
        python3-wheel \
        python3-markdown \
        python3-mock \
        python3-phonenumbers \
        python3-websocket \
        python3-google-auth \
        libpq-dev \
        pylint \
        python3-jwt \
        python3-asn1crypto \
        python3-html2text \
        python3-suds \
        python3-xmlsec \
        python3-markdown2 \
        python3-aiosmtpd \
        # Moved packages
        python3-venv \
        # Extra packages
        python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

# Install wkhtmltopdf
RUN curl -sSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb -o /tmp/wkhtml.deb \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y install --no-install-recommends --fix-missing -qq \
        /tmp/wkhtml.deb \
    && rm -rf /var/lib/apt/lists/* \
    && rm /tmp/wkhtml.deb

# Install npm packages
RUN npm install --force -g \
        rtlcss@3.4.0 \
        es-check@6.0.0 \
        eslint@8.1.0 \
        prettier@2.7.1 \
        eslint-config-prettier@8.5.0 \
        eslint-plugin-prettier@4.2.1

# Install Debian packages in debian/control and get latest postgresql-client
ADD https://raw.githubusercontent.com/odoo/odoo/18.0/debian/control /tmp/control_18.txt
ADD https://raw.githubusercontent.com/odoo/odoo/master/debian/control /tmp/control_master.txt
RUN curl -sSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /etc/apt/trusted.gpg.d/psql_client.asc \
    && echo "deb http://apt.postgresql.org/pub/repos/apt/ `lsb_release -s -c`-pgdg main" > /etc/apt/sources.list.d/pgclient.list \
    && apt-get update \
    && sed -n '/^Depends:/,/^[A-Z]/p' /tmp/control_18.txt \
        | awk '/^ [a-z]/ { gsub(/,/,"") ; gsub(" ", "") ; print $NF }' | sort -u \
        | DEBIAN_FRONTEND=noninteractive xargs apt-get install -y -qq --no-install-recommends \
    && sed -n '/^Depends:/,/^[A-Z]/p' /tmp/control_master.txt \
        | awk '/^ [a-z]/ { gsub(/,/,"") ; gsub(" ", "") ; print $NF }' | sort -u \
        | DEBIAN_FRONTEND=noninteractive xargs apt-get install -y -qq --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome
RUN curl -sSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb \
    && apt-get update \
    && apt-get -y install --no-install-recommends \
        /tmp/chrome.deb \
    && rm /tmp/chrome.deb

# Install Odoo Phonenumbers
ADD https://packages.odoo.com/pub/python3-phonenumbers_8.12.57-4+odoo1_all.deb /phonenumbers.deb
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        /phonenumbers.deb \
    && rm -rf /var/lib/apt/lists/*

# Install uv system-wide
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

# Create virtual environments using uv
RUN uv venv /venv/odoo --system-site-packages \
    && uv venv /venv/odoo-doc --system-site-packages

# Install Odoo Python requirements
ADD https://raw.githubusercontent.com/odoo/odoo/18.0/requirements.txt /tmp/18_requirements.txt
ADD https://raw.githubusercontent.com/odoo/odoo/master/requirements.txt /tmp/master_requirements.txt
ADD https://raw.githubusercontent.com/odoo/documentation/master/requirements.txt /tmp/doc_requirements.txt
ADD https://raw.githubusercontent.com/odoo/documentation/master/tests/requirements.txt /tmp/doctests_requirements.txt
RUN VIRTUAL_ENV=/venv/odoo uv pip install --no-cache-dir \
        -r /tmp/18_requirements.txt \
        -r /tmp/master_requirements.txt \
    && VIRTUAL_ENV=/venv/odoo-doc uv pip install --no-cache-dir \
        -r /tmp/18_requirements.txt \
        -r /tmp/master_requirements.txt \
    && VIRTUAL_ENV=/venv/odoo-doc uv pip install --no-cache-dir \
        -r /tmp/doc_requirements.txt \
        -r /tmp/doctests_requirements.txt

# Install pip packages
RUN uv pip install --no-cache-dir \
        # Runbot packages
        ebaysdk==2.1.5 \
        pdf417gen==0.7.1 \
        astroid==3.3.9 \
        pylint==3.3.6 \
        unidiff==0.7.3 \
        paramiko==2.12.0 \
        markdown2==2.4.11 \
        # Extra packages
        debugpy \
        pydevd-odoo \
        watchdog \
        inotify

# Remove the default Ubuntu user, add an Odoo user and set up their environment
RUN --mount=type=bind,source=append.bashrc,target=/tmp/append.bashrc \
    userdel ubuntu \
    && groupadd -g 1000 odoo \
    && useradd --create-home -u 1000 -g odoo -G audio,video odoo \
    && passwd -d odoo \
    && echo odoo ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/odoo \
    && chmod 0440 /etc/sudoers.d/odoo \
    # Create the working directory and make it owned by the Odoo user
    && mkdir /code \
    && chown odoo:odoo /code \
    # Give the new user ownership of the venv directory
    && chown -R odoo:odoo /venv \
    # Configure the Bash shell using Starship
    && curl -sS https://starship.rs/install.sh | sh -s -- --yes \
    && cat /tmp/append.bashrc >> /home/odoo/.bashrc

# Switch to the odoo user for subsequent commands
USER odoo

# Create mounted folders to prevent permission issues
RUN mkdir -p /home/odoo/.local/share && \
    mkdir -p /home/odoo/.local/share/Odoo && \
    mkdir -p /home/odoo/.local/bin && \
    mkdir -p /home/odoo/.bash_history_data && \
    mkdir -p /home/odoo/.ssh

# Copy config files and scripts
COPY .bash_aliases /home/odoo/.bash_aliases
COPY starship.toml /home/odoo/.config/starship.toml
COPY startup.sh /home/odoo/.local/bin/startup.sh

WORKDIR /code

# Expose useful ports
EXPOSE 5678 8075 8076 8077 8078 8079

# Set Tini as the entrypoint
ENTRYPOINT ["/usr/bin/tini", "--"]

# Start background services and keep the container alive
CMD ["sh", "-c", "/home/odoo/.local/bin/startup.sh"]
