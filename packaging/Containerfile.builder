# Builder image for Code Companion .rpm/.deb packages.
#
# Fedora base carrying fpm (+ ruby) and the tools fpm needs to emit both formats:
#   - rpm target:  rpmbuild (rpm-build), cpio
#   - deb target:  ar (binutils), tar, gzip/xz  -- fpm's deb backend does NOT need dpkg
# python3-pip is used to fetch the vendored PyPI-only deps (pure + manylinux wheels).
#
# Nothing here is distro-specific to the *output*: the produced staging tree contains only
# app source + portable wheels, so a single builder emits packages that install on both
# Fedora and Ubuntu (system gi/pygit2/GTK come in as declared dependencies).
FROM fedora:41

RUN dnf install -y --setopt=install_weak_deps=False \
        ruby ruby-devel rubygems rubygem-json \
        gcc make \
        rpm-build cpio \
        binutils tar gzip xz \
        python3 python3-pip \
    && gem install --no-document fpm \
    && dnf clean all

WORKDIR /work
