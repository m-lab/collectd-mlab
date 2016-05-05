#
# The collectd-mlab RPM spec file.
#
%define name collectd-mlab
%define slicename mlab_utility
%define version 1.5
%define taglevel alpha
%define releasetag %{taglevel}%{?date:.%{date}}

# NOTE: Disable the brp-python-bytecompile script (which creates *.pyc, *.pyo).
# NOTE: At package-creation time, this directive rewrites the __os_install_post
# macro from /usr/lib/rpm/redhat/macros. This directive removes the
# brp-python-bytecode script so that it does not run.
%global __os_install_post %( \
    echo '%{__os_install_post}' | \
    sed -e 's!/usr/lib[^[:space:]]*/brp-python-bytecompile[[:space:]].*$!!g' )

%define site_packages %( \
    python -c "from distutils.sysconfig import get_python_lib; print(get_python_lib())" )

Vendor: Measurement Lab
Packager: Measurement Lab <support@measurementlab.net>
Distribution: Measurement Lab CentOS 6
URL: http://www.measurementlab.net

Summary: Support and configuration for resource utilization on M-Lab nodes.
Name: %{name}
Version: %{version}
Release: %{releasetag}
License: Apache License
Group: System Environment/Base
BuildArch: noarch

# This is for CentOS 6 only. Package names may change in future distros.
Requires: cronie
Requires: crontabs
Requires: yum-cron
Requires: collectd
Requires: collectd-web
Requires: collectd-rrdtool
Requires: collectd-snmp
Requires: python-rrdtool
Requires: python-gflags
Requires: python-netifaces
Requires: net-snmp-python
Requires: PyYAML

Source0: %{name}-%{version}.tar.bz2

%description
The collectd-mlab package provides resource utilization collection, export and
monitoring for M-Lab servers. The package includes configuration, a collectd
plugin, and processes for periodic data export.

%prep
%setup

%build

%install
#
# Files for "collectd-mlab" package.
#

# Overview and license.
install -D -m 644 README.md	%{buildroot}/usr/share/collectd-mlab/README.md
install -D -m 644 LICENSE	%{buildroot}/usr/share/collectd-mlab/LICENSE

# Data export scripts.
install -D -m 755 export/mlab_export_cleanup.cron	%{buildroot}/etc/cron.daily/mlab_export_cleanup.cron
install -D -m 644 export/mlab_export.cron		%{buildroot}/etc/cron.d/mlab_export.cron
install -D -m 755 export/mlab_export.py			%{buildroot}/usr/bin/mlab_export.py
install -D -m 644 export/export_metrics.conf		%{buildroot}/usr/share/collectd-mlab/export_metrics.conf
install -D -m 644 export/mlab_collectd_watchdog.cron	%{buildroot}/etc/cron.d/mlab_collectd_watchdog.cron
install -D -m 755 export/mlab_collectd_watchdog.sh		%{buildroot}/usr/share/collectd-mlab/mlab_collectd_watchdog.sh

# Configuration for *limited* access to collectd-web.
install -D -m 755 viewer/mlab-view 			%{buildroot}/usr/bin/mlab-view
install -D -m 644 viewer/httpd.collectd.conf		%{buildroot}/usr/share/collectd-mlab/httpd.conf
install -D -m 644 viewer/collection-mlab.conf		%{buildroot}/etc/collection-mlab.conf

# Collectd configuration.
install -D -m 644 plugin/collectd			%{buildroot}/etc/default/collectd
install -D -m 644 plugin/collectd-mlab.conf		%{buildroot}/etc/collectd-mlab.conf
install -D -m 644 plugin/types.db			%{buildroot}/usr/share/collectd-mlab/types.db
install -D -m 644 plugin/mlab.py			%{buildroot}/var/lib/collectd/python/mlab_vs.py

# Init script.
install -D -m 755 plugin/collectd-setup		%{buildroot}/etc/init.d/collectd-setup

# Python site-packages modules.
install -d %{buildroot}/%{site_packages}/mlab/disco
install -D -m 644 site-packages/mlab/disco/__init__.py \
                  site-packages/mlab/disco/arp.py \
                  site-packages/mlab/disco/collectd.py \
                  site-packages/mlab/disco/discovery.py \
                  site-packages/mlab/disco/models.py \
                  site-packages/mlab/disco/network.py \
                  site-packages/mlab/disco/route.py \
                  site-packages/mlab/disco/simple_session.py \
                  %{buildroot}/%{site_packages}/mlab/disco

# Disco commands.
install -D -m 755 disco/disco_config.py	%{buildroot}/usr/bin/disco_config.py

# Disco config.
install -D -m 644 disco/models.yaml \
    %{buildroot}/usr/share/collectd-mlab/models.yaml

#
# Files for "collectd-mlab-vsys" package.
#

# Monitoring.
install -D -m 755 monitoring/check_collectd_mlab.py \
    %{buildroot}/usr/lib/nagios/plugins/check_collectd_mlab.py

# Vsys backend.
install -D -m 755 system/vsys/vs_resource_backend.py \
    %{buildroot}/vsys/vs_resource_backend

%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(-,root,root)

/usr/share/collectd-mlab/README.md
/usr/share/collectd-mlab/LICENSE

# Data export scripts.
/usr/bin/mlab_export.py
/usr/share/collectd-mlab/export_metrics.conf
/usr/share/collectd-mlab/mlab_collectd_watchdog.sh
/etc/cron.d/mlab_export.cron
/etc/cron.d/mlab_collectd_watchdog.cron
/etc/cron.daily/mlab_export_cleanup.cron

# Configuration for *limited* access to collectd-web.
/usr/bin/mlab-view
/usr/share/collectd-mlab/httpd.conf
/etc/collection-mlab.conf

# Collectd configuration.
/var/lib/collectd/python/mlab_vs.py
/etc/default/collectd
/etc/collectd-mlab.conf
/usr/share/collectd-mlab/types.db

# Init script.
/etc/init.d/collectd-setup

# Python site-packages modules.
%{site_packages}/mlab/disco/__init__.py
%{site_packages}/mlab/disco/arp.py
%{site_packages}/mlab/disco/collectd.py
%{site_packages}/mlab/disco/discovery.py
%{site_packages}/mlab/disco/models.py
%{site_packages}/mlab/disco/network.py
%{site_packages}/mlab/disco/route.py
%{site_packages}/mlab/disco/simple_session.py

# Disco commands.
/usr/bin/disco_config.py

# Disco configs.
/usr/share/collectd-mlab/models.yaml

%post

# NOTE: The collectd-web rpm owns /etc/collection.conf and COLLECTD_LINK.
# Trying to claim the same file in %files section creates an RPM conflict
# error. So, this section changes the link destination.
COLLECTD_LINK="/usr/share/collectd/collection3/etc/collection.conf"
if test -f "${COLLECTD_LINK}" ; then
  ln -f -s /etc/collection-mlab.conf "${COLLECTD_LINK}"
fi

chkconfig crond on
chkconfig rsyslog on
chkconfig collectd on
chkconfig collectd-setup on

# TODO(soltesz): fix mlab_export.py to handle initial conditions correctly.
# Initialize the lastexport timestamp file to one hour ago.
touch -t $( date +%Y%m%d%H00 -d "-1 hour" ) /var/lib/collectd/lastexport.tstamp

# TODO(soltesz): what package should own this file?
touch %{site_packages}/mlab/__init__.py

# NOTE: Only run these steps if in a development environment.
if test -n "${DEVEL_ENVIRONMENT}" ; then
  if ! test -d /var/spool/%{slicename} ; then
    # Normally this would be created by the slice package.
    mkdir -p /var/spool/%{slicename}
  fi

  service crond start
  service rsyslog start
  service collectd-setup start
  service collectd start
fi


%package host
Summary: The vsys backend scripts to support collectd-mlab.
Requires: python
Requires: vsys
# TODO: add a dependency in nodebase on package "collectd-mlab-vsys".

%description host
The collectd-mlab-vsys package includes support scripts that should run in the
root context of the M-Lab server.

%files host
# Vsys backend.
/vsys/vs_resource_backend

# Monitoring.
/usr/lib/nagios/plugins/check_collectd_mlab.py

%pre host
# Check if we're trying to install the vsys packate in a guest context.
if test -f /dev/hdv1 ; then
  echo "WARNING: The vsys backend should be installed in host (root) context."
  echo "WARNING: collectd-mlab is incomplete without the vsys backend."
fi

%package devel
Summary: A place to record dependencies for developing collectd-mlab.
Requires: python-mock
Requires: python-coverage
Requires: pylint
Requires: rpm-build

%description devel
All dependencies for developing the collectd-mlab plugin.

%files devel

%changelog
* Sat Nov 01 2014 Stephen Soltesz <soltesz@google.com> collectd-mlab-0.99-alpha
- First public version of collectd-mlab package.
