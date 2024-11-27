# Ubuntu 24.04 LTS (Noble Numbat)
FROM ubuntu:noble

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    NODE_ENV=production \
    TERM=xterm-256color \
    # Keeps Python from generating .pyc files in the container
    PYTHONDONTWRITEBYTECODE=1 \
    # Turns off buffering for easier container logging
    PYTHONUNBUFFERED=1

# Add flamegraph
ADD --chmod=555 https://raw.githubusercontent.com/brendangregg/FlameGraph/master/flamegraph.pl /usr/local/bin/flamegraph.pl

# Add GeoIP databases
ADD https://github.com/maxmind/MaxMind-DB/raw/main/test-data/GeoIP2-City-Test.mmdb /usr/share/GeoIP/GeoLite2-City.mmdb
ADD https://github.com/maxmind/MaxMind-DB/raw/main/test-data/GeoIP2-Country-Test.mmdb /usr/share/GeoIP/GeoLite2-Country.mmdb

RUN apt-get update -y && \
    apt-get upgrade -y && \
    # Install curl to fetch custom Debian packages
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl && \
    # Fetch Google Chrome (for web tour tests)
    curl -sSL https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_123.0.6312.58-1_amd64.deb \
        -o chrome.deb && \
    # Fetch the right version of wkhtmltox
    curl -sSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb \
        -o wkhtmltox.deb && \
    # Continue install after fetching debs
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        # Install useful tools and optional Odoo dependencies
        bash-completion \
        build-essential \
        ffmpeg \
        flake8 \
        file \
        gawk \
        gettext \
        gnupg2 \
        libjpeg9-dev \
        libldap2-dev \
        libpq-dev \
        libsasl2-dev \
        libxslt1-dev \
        nano \
        pipx \
        postgresql-client \
        publicsuffix \
        python3-google-auth \
        python3-pdfminer \
        python3-setuptools \
        python3-wheel \
        sed \
        sudo \
        unzip \
        vim \
        zip \
        zlib1g-dev \
        # Install Python dependencies for Odoo
        pylint \
        python3-aiosmtpd \
        python3-asn1crypto \
        python3-astroid \
        python3-babel \
        python3-cbor2 \
        python3-dateutil \
        python3-dbfread \
        python3-decorator \
        python3-dev \
        python3-docopt \
        python3-docutils \
        python3-feedparser \
        python3-fonttools \
        python3-freezegun \
        python3-geoip2 \
        python3-gevent \
        python3-html2text \
        python3-jinja2 \
        python3-jwt \
        python3-libsass \
        python3-lxml \
        python3-lxml-html-clean \
        python3-mako \
        python3-markdown \
        python3-matplotlib \
        python3-num2words \
        python3-ofxparse \
        python3-openid \
        python3-openpyxl \
        python3-openssl \
        python3-passlib \
        python3-pdfminer \
        python3-phonenumbers \
        python3-pil \
        python3-polib \
        python3-psutil \
        python3-psycogreen \
        python3-psycopg2 \
        python3-pydot \
        python3-pyldap \
        python3-pyparsing \
        python3-pypdf2 \
        python3-qrcode \
        python3-renderpm \
        python3-reportlab \
        python3-requests \
        python3-rjsmin \
        python3-setproctitle \
        python3-simplejson \
        python3-slugify \
        python3-stdnum \
        python3-suds \
        python3-tz \
        python3-unittest2 \
        python3-vobject \
        python3-websocket \
        python3-werkzeug \
        python3-xlrd \
        python3-xlsxwriter \
        python3-xlwt \
        python3-xmlsec \
        python3-zeep \
        # Install Python dependencies for the documentation
        python3-pygments \
        python3-sphinx \
        python3-sphinx-tabs \
        # Set python3 by default
        python-is-python3 \
        # Install pip, to install python dependencies not packaged by Ubuntu
        python3-pip \
        # Install lessc
        node-less \
        # Install npm, to install node dependencies not packaged by Ubuntu
        npm \
        # Install fonts
        fonts-freefont-ttf \
        fonts-khmeros-core \
        fonts-noto-cjk \
        fonts-ocr-b \
        fonts-vlgothic \
        gsfonts \
        # Install Google Chrome
        ./chrome.deb \
        # Install wkhtmltox
        ./wkhtmltox.deb

# Cleanup
RUN rm -rf ./chrome.deb ./wkhtmltox.deb /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Node dependencies
RUN npm install -g \
        # Install dependencies for Odoo
        rtlcss@3.4.0 \
        # Install dependencies to check/lint code
        eslint@8.27.0 \
        eslint-config-prettier@8.5.0 \
        eslint-plugin-prettier@4.2.1 \
        prettier@2.7.1

# Remove the default Ubuntu user, add an Odoo user and set up his environment
RUN --mount=type=bind,source=starship.bashrc,target=/tmp/starship.bashrc \
    userdel ubuntu && \
    groupadd -g 1000 odoo && \
    useradd --create-home -u 1000 -g odoo -G audio,video odoo && \
    passwd -d odoo && \
    echo odoo ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/odoo && \
    chmod 0440 /etc/sudoers.d/odoo && \
    # Create the working directory and make it owned by the Odoo user
    mkdir /code && \
    chown odoo /code && \
    # Configure the Bash shell using Starship
    curl -sS https://starship.rs/install.sh | sh -s -- --yes && \
    cat /tmp/starship.bashrc >> /home/odoo/.bashrc

# Install the following dependencies using the "odoo" user
USER odoo

# Set the right path for the installed tools
ENV PATH="/home/odoo/bin:/home/odoo/.local/bin:$PATH"

# Install Python dependencies via pip for packages not available via apt
RUN pip install --no-cache-dir --break-system-packages \
        # Install Odoo dependencies
        ebaysdk \
        firebase-admin==2.17.0 \
        inotify \
        pdf417gen \
        # Install documentation dependencies
        pygments-csv-lexer~=0.1 \
        sphinxcontrib-applehelp==1.0.4 \
        sphinxcontrib-devhelp==1.0.2 \
        sphinxcontrib-htmlhelp==2.0.1 \
        sphinxcontrib-serializinghtml==1.1.5 \
        sphinxcontrib-qthelp==1.0.3 \
        # Install debug tools
        debugpy

# Create the Odoo data folder already to prevent permission issues
RUN mkdir -p /home/odoo/.local/share/Odoo

WORKDIR /code

# Expose useful ports
EXPOSE 5678 8069 8076 8077 8078 8079

# Install Odoo Toolkit before startin the shell (to always have the latest version)
CMD ["sh", "-c", "pipx install --force --pip-args '--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/' odoo-toolkit && otk --install-completion && exec bash"]
