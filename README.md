collectd-mlab
=============

The collectd-mlab package provides a collectd plugin and associated scripts for
continuous monitoring and periodic export of metrics. This package is designed
to run in the M-Lab experiment environment.

The primary contents are:

 * `config/mlab.py` -- collectd-python plugin, which harvests vserver-specific
   resource usage.

 * `system/vsys/vs_resource_backend.py` -- vsys backend, which reports
   vserver-specific information not directly available to the experiment
   environment.

 * `export/mlab_export.py` -- metric export script, which periodically converts
   RRD metrics to JSON.

 * `monitoring/check_collectd_mlab.py` -- meta-monitoring nagios plugin, which
   verifies the resource monitoring system is working well.

 * `*_test.py` -- unit tests for all the above.

 * essential configuration and cron scripts to integrate everything.

Packages
========

Three packages are created by `make rpm`.

 * `collectd-mlab` -- the primary package that should be installed in the
   mlab_utility slice.

 * `collectd-mlab-vsys` -- The vsys package includes the vs_resourc_backend
   script and nagios plugin. This should be installed in the host context.

 * `collectd-mlab-devel` -- A pseudo package that includes the list of required
   packages for developing this plugin.

Creation & Development
======================

The collectd-mlab package was created to run in a CentOS 6 environment. Some
adaptation will be needed to build or test in another environment.

Style
=====

Python files should follow the Google Python Style Guide:

  https://google-styleguide.googlecode.com/svn/trunk/pyguide.html

Some exceptions are embedded in the collect-mlab source or encoded in .pylintrc.

If you find examples where this is not the case, please report it as a bug.
