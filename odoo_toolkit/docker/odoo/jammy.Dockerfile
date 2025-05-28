# Ubuntu 22.04 LTS (Jammy Jellyfish)
FROM ubuntu:jammy

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    NODE_ENV=production \
    TERM=xterm-256color \
    # Keeps Python from generating .pyc files in the container
    PYTHONDONTWRITEBYTECODE=1 \
    # Turns off buffering for easier container logging
    PYTHONUNBUFFERED=1

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
    curl -sSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
        -o chrome.deb

RUN --mount=type=bind,source=wkhtmltox_0.12.6.1-2.jammy_amd64.deb,target=/tmp/wkhtmltox.deb \
    apt-get update -y && \
    apt-get upgrade -y && \
    # Continue install after fetching Debian packages
    apt-get install -y --no-install-recommends \
        #==============================================
        # Install necessary and useful Debian packages
        #==============================================
        file \
        gettext \
        git \
        nano \
        postgresql-client \
        sed \
        # To route localhost routes to other containers
        socat \
        sudo \
        # To properly manage background processes (socat)
        tini \
        vim \
        #
        #======================================
        # Install Python dependencies for Odoo
        #======================================
        # pylint==2.12.2 (test dep)
        pylint \
        # aiosmtpd==1.4.2 (test dep)
        python3-aiosmtpd \
        # asn1crypto==1.4.0
        python3-asn1crypto \
        # astroid==2.9.3 (test dep)
        python3-astroid \
        # Babel==2.8.0
        python3-babel \
        # cbor2==5.4.2
        python3-cbor2 \
        # chardet==4.0.0
        python3-chardet \
        # cryptography==3.4.8
        python3-cryptography \
        # python-dateutil==2.8.1
        python3-dateutil \
        # dbfread==2.0.7 (account_winbooks_import)
        python3-dbfread \
        # decorator==4.4.2
        python3-decorator \
        # docutils==0.17.1
        python3-docutils \
        # fonttools==4.29.1 (odoo/tools/pdf)
        python3-fonttools \
        # freezegun==1.1.0
        python3-freezegun \
        # geoip2==2.9.0
        python3-geoip2 \
        # gevent==21.8.0
        python3-gevent \
        # google-auth==1.5.1 (cloud_storage_google, social_push_notifications)
        python3-google-auth \
        # greenlet==1.1.2
        python3-greenlet \
        # idna==3.3.1
        python3-idna \
        # Jinja2==3.0.3
        python3-jinja2 \
        # jwt==2.3.0 (l10n_be_hr_payroll_dimona, l10n_in_qr_code_bill_scan, l10n_in_reports_gstr)
        python3-jwt \
        # libsass==0.20.1
        python3-libsass \
        # lxml==4.8.0
        python3-lxml \
        # markdown==3.3.6 (upgrade, upgrade-util)
        python3-markdown \
        # MarkupSafe==2.0.1
        python3-markupsafe \
        # num2words==0.5.10
        python3-num2words \
        # ofxparse==0.21
        python3-ofxparse \
        # openpyxl==3.0.9
        python3-openpyxl \
        # pyopenssl==21.0.0
        python3-openssl \
        # paramiko==2.9.3
        python3-paramiko \
        # passlib==1.7.4
        python3-passlib \
        # pdfminer.six==20220319 (attachment_indexation)
        python3-pdfminer \
        # phonenumbers==8.12.1 (account_peppol, data_cleaning, phone_validation)
        python3-phonenumbers \
        # Pillow==9.0.1
        python3-pil \
        # polib==1.1.1
        python3-polib \
        # psutil==5.9.0
        python3-psutil \
        # psycopg2==2.9.2
        python3-psycopg2 \
        # python-ldap==3.2.0
        python3-pyldap \
        # PyPDF2==1.26.0
        python3-pypdf2 \
        # qrcode==7.3.1
        python3-qrcode \
        # reportlab==3.6.8
        python3-reportlab \
        # requests==2.25.1
        python3-requests \
        # rjsmin==1.1.0
        python3-rjsmin \
        # pyserial==3.5
        python3-serial \
        # python-slugify==4.0.0 (base)
        python3-slugify \
        # python-stdnum==1.17
        python3-stdnum \
        # suds==1.0.0 (fallback in odoo/_monkeypatches)
        python3-suds \
        # pytz==2022.1
        python3-tz \
        # urllib3==1.26.5
        python3-urllib3 \
        # pyusb==1.2.1
        python3-usb \
        # vobject==0.9.6.1
        python3-vobject \
        # websocket==1.2.3 (test dep + hw_drivers)
        python3-websocket \
        # Werkzeug==2.0.2
        python3-werkzeug \
        # xlrd==1.2.0
        python3-xlrd \
        # XlsxWriter==3.0.2
        python3-xlsxwriter \
        # xlwt==1.3.0
        python3-xlwt \
        # xmlsec==1.3.12 (l10n_nl_reports_sbr)
        python3-xmlsec \
        # zeep==4.1.0
        python3-zeep \
        #
        #===================================================
        # Install Python dependencies for the documentation
        #===================================================
        # pygments==2.11.2
        python3-pygments \
        # sphinx==4.3.2
        python3-sphinx \
        # sphinx-tabs==3.2.0
        python3-sphinx-tabs \
        #
        #=============================================================================================
        # Install pip and build tools, to install Python dependencies not available as Debian package
        #=============================================================================================
        build-essential \
        pipx \
        python3-dev \
        # Use python3 by default
        python-is-python3 \
        python3-pip \
        python3-setuptools \
        python3-wheel \
        #
        #===========================================
        # Install npm, to install node dependencies
        #===========================================
        npm \
        #
        #===============
        # Install fonts
        #===============
        # FreeFont (FreeSerif, FreeSans, FreeMono)
        fonts-freefont-ttf \
        # Khmer OS (Khmer language spoken in Cambodia)
        fonts-khmeros-core \
        # Noto CJK (Chinese, Japanese and Korean)
        fonts-noto-cjk \
        # OCR-B (Barcodes)
        fonts-ocr-b \
        # VL Gothic (Japanese)
        fonts-vlgothic \
        # Replacing Times New Roman, Palatino, Century Schoolbook, Helvetica, Avant Garde, Courier ...
        gsfonts \
        # matplotlib==3.6.3 (DejaVu fonts)
        python3-matplotlib \
        # Fonts like Courier, Helvetica, Times and Lucida
        xfonts-75dpi \
        #
        #=======================
        # Install Google Chrome
        #=======================
        ./chrome.deb \
        #
        #===================
        # Install wkhtmltox
        #===================
        /tmp/wkhtmltox.deb

# Cleanup
RUN rm -rf ./chrome.deb /var/lib/apt/lists/* /tmp/* /var/tmp/*

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
RUN --mount=type=bind,source=append.bashrc,target=/tmp/append.bashrc \
    groupadd -g 1000 odoo && \
    useradd --create-home -u 1000 -g odoo -G audio,video odoo && \
    passwd -d odoo && \
    echo odoo ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/odoo && \
    chmod 0440 /etc/sudoers.d/odoo && \
    # Create the working directory and filestore directory and make it owned by the Odoo user
    mkdir -p /code && \
    chown odoo /code && \
    # Configure the Bash shell using Starship
    curl -sS https://starship.rs/install.sh | sh -s -- --yes && \
    cat /tmp/append.bashrc >> /home/odoo/.bashrc

# Install the following dependencies using the "odoo" user
USER odoo

# Set the right path for the installed tools
ENV PATH="/home/odoo/.local/bin:$PATH"

# Install Python dependencies via pip for packages not available via apt
RUN pip install --no-cache-dir \
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
        # Install development tools
        debugpy \
        pydevd-odoo \
        watchdog

# Create mounted folders to prevent permission issues
RUN mkdir -p /home/odoo/.local/share/Odoo && \
    mkdir -p /home/odoo/.bash_history_data

WORKDIR /code

# Expose useful ports
EXPOSE 5678 8070 8071 8072 8073 8074

# Set Tini as the entrypoint
ENTRYPOINT ["/usr/bin/tini", "--"]

# Start socat to forward port 25 to mailpit:1025,
# install latest Odoo Toolkit and set up completion,
# and keep the server running
CMD ["sh", "-c", "/home/odoo/.local/bin/startup.sh"]
