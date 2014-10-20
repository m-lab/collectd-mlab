# Copyright 2014 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# Makefile for M-Lab, collectd plugin package.
#
# To build this package the following dependencies are necessary:
#  * rpm-build, python-mock, python-coverage, pylint
#
# The build environment should be CentOS 6, with the same architecture and
# version as the deployment environment, or results are unknown.

# NOTE: use sensible defaults in case they're not already set.
SPECFILE = collectd-mlab.spec
RPMBUILD ?= $(PWD)/../build
TMPBUILD ?= $(PWD)/../build/tmp
SOURCE_DIR ?= $(PWD)

package := $(shell rpm -q --qf "%{NAME}\n" --specfile $(SPECFILE) | head -1)
version := $(shell rpm -q --qf "%{VERSION}\n" --specfile $(SPECFILE) | head -1)
release := $(shell rpm -q --qf "%{RELEASE}\n" --specfile $(SPECFILE) | head -1)
arch := $(shell rpm -q --qf "%{ARCH}\n" --specfile $(SPECFILE) | head -1)
PKGNAME = ${package}-${version}
TARNAME = $(PKGNAME).tar
BZ2NAME = $(TARNAME).bz2
RPMNAME = $(PKGNAME)-${release}.${arch}.rpm

PKGDIR := $(TMPBUILD)/$(PKGNAME)
TARFILE := $(TMPBUILD)/$(TARNAME)
BZ2FILE := $(TMPBUILD)/$(BZ2NAME)
RPMFILE := $(RPMBUILD)/$(arch)/$(RPMNAME)

RPMDEFS := --define "_sourcedir $(TMPBUILD)" --define "_builddir $(TMPBUILD)"
RPMDEFS += --define "_srcrpmdir $(TMPBUILD)" --define "_rpmdir $(RPMBUILD)"

.PHONY: all clean

all: test rpm


test:
	./runtests  # A new RPM depends on passing tests.


rpm: $(RPMFILE)


$(RPMFILE): $(BZ2FILE)
	rpmbuild $(RPMDEFS) -bb $(SPECFILE)


$(BZ2FILE): $(TARFILE)
	echo 'bzip' $@ $(TARFILE)
	bzip2 --keep --force $(TARFILE)


FILES := $(shell find ./ -name "*.py" -o -name "*.conf" -o -name "*.cron" )
# NOTE: Copy local files info pkg dir.
EXCLUDES := --exclude ".*.sw*" --exclude ".git" --exclude ".svn"
$(TARFILE): collectd-mlab.spec $(FILES)
	mkdir -p $(PKGDIR)
	rsync -ar $(EXCLUDES) $(SOURCE_DIR)/ $(TMPBUILD)/$(PKGNAME)/
	tar --exclude "*.tar" -cvf $@ -C $(TMPBUILD) $(PKGNAME)


clean:
	rm -rf $(PKGDIR)
	rm -f $(TARFILE)
	rm -f $(BZ2FILE)
	rm -f $(RPMFILE)
