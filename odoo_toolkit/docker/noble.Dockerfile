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
        # pylint==3.0.3 (test dep)
        pylint \
        # aiosmtpd==1.4.4 (test dep)
        python3-aiosmtpd \
        # asn1crypto==1.5.1
        python3-asn1crypto \
        # astroid==3.0.2 (test dep)
        python3-astroid \
        # Babel==2.10.3
        python3-babel \
        # cbor2==5.6.2
        python3-cbor2 \
        # chardet==5.2.0
        python3-chardet \
        # cryptography==42.0.8
        python3-cryptography \
        # python-dateutil==2.8.2
        python3-dateutil \
        # dbfread==2.0.7 (account_winbooks_import)
        python3-dbfread \
        # decorator==5.1.1
        python3-decorator \
        # docutils==0.20.1
        python3-docutils \
        # fonttools==4.46.0 (odoo/tools/pdf)
        python3-fonttools \
        # freezegun==1.2.1
        python3-freezegun \
        # geoip2==2.9.0
        python3-geoip2 \
        # gevent==24.2.1
        python3-gevent \
        # google-auth==1.5.1 (cloud_storage_google, social_push_notifications)
        python3-google-auth \
        # greenlet==3.0.3
        python3-greenlet \
        # idna==3.6
        python3-idna \
        # Jinja2==3.1.2
        python3-jinja2 \
        # jwt==2.7.0 (l10n_be_hr_payroll_dimona, l10n_in_qr_code_bill_scan, l10n_in_reports_gstr)
        python3-jwt \
        # libsass==0.22.0
        python3-libsass \
        # lxml==5.2.1
        python3-lxml \
        # lxml-html-clean
        python3-lxml-html-clean \
        # markdown==3.5.2 (upgrade, upgrade-util)
        python3-markdown \
        # MarkupSafe==2.1.5
        python3-markupsafe \
        # num2words==0.5.13
        python3-num2words \
        # ofxparse==0.21
        python3-ofxparse \
        # openpyxl==3.1.2
        python3-openpyxl \
        # pyopenssl==24.1.0
        python3-openssl \
        # passlib==1.7.4
        python3-passlib \
        # pdfminer.six==20221105 (attachment_indexation)
        python3-pdfminer \
        # phonenumbers==8.12.57 (account_peppol, data_cleaning, phone_validation)
        python3-phonenumbers \
        # Pillow==10.2.0
        python3-pil \
        # polib==1.1.1
        python3-polib \
        # psutil==5.9.8
        python3-psutil \
        # psycopg2==2.9.9
        python3-psycopg2 \
        # python-ldap==3.4.4
        python3-pyldap \
        # PyPDF2==2.12.1
        python3-pypdf2 \
        # qrcode==7.4.2
        python3-qrcode \
        # reportlab==4.1.0
        python3-reportlab \
        # requests==2.31.0
        python3-requests \
        # rjsmin==1.2.0
        python3-rjsmin \
        # pyserial==3.5
        python3-serial \
        # python-slugify==8.0.4 (base)
        python3-slugify \
        # python-stdnum==1.19
        python3-stdnum \
        # suds==1.1.2 (fallback in odoo/_monkeypatches)
        python3-suds \
        # pytz==2024.1
        python3-tz \
        # urllib3==2.0.7
        python3-urllib3 \
        # pyusb==1.2.1
        python3-usb \
        # vobject==0.9.6.1
        python3-vobject \
        # websocket==1.7.0 (test dep + hw_drivers)
        python3-websocket \
        # Werkzeug==3.0.1
        python3-werkzeug \
        # xlrd==2.0.1
        python3-xlrd \
        # XlsxWriter==3.1.9
        python3-xlsxwriter \
        # xlwt==1.3.0
        python3-xlwt \
        # xmlsec==1.3.13 (l10n_nl_reports_sbr)
        python3-xmlsec \
        # zeep==4.2.1
        python3-zeep \
        #
        #===================================================
        # Install Python dependencies for the documentation
        #===================================================
        # pygments==2.17.2
        python3-pygments \
        # sphinx==7.2.6
        python3-sphinx \
        # sphinx-tabs==3.4.4
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
    cat /tmp/append.bashrc >> /home/odoo/.bashrc

# Install the following dependencies using the "odoo" user
USER odoo

# Set the right path for the installed tools
ENV PATH="/home/odoo/bin:/home/odoo/.local/bin:$PATH"

# Install Python dependencies via pip that are not available as Debian package or need a newer version
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
        # Install development tools
        debugpy \
        pydevd-odoo \
        watchdog

# Create mounted folders to prevent permission issues
RUN mkdir -p /home/odoo/.local/share/Odoo && \
    mkdir -p /home/odoo/.bash_history_data

WORKDIR /code

# Expose useful ports
EXPOSE 5678 8075 8076 8077 8078 8079

# Set Tini as the entrypoint
ENTRYPOINT ["/usr/bin/tini", "--"]

# Start socat to forward port 25 to mailpit:1025
# Install latest Odoo Toolkit and set up completion
# Keep the server running
CMD ["sh", "-c", "socat TCP-LISTEN:25,fork TCP:mailpit:1025 & \
                  pipx install --force odoo-toolkit && \
                  otk --install-completion && \
                  tail -f /dev/null"]
