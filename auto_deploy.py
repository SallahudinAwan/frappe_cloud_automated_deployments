#!/usr/bin/env python3
"""
Automated Deployment runner (entrypoint).

Logic lives in `frappe_cloud_deploy_middleware/frappe_cloud/deployer.py`.
"""

from frappe_cloud_deploy_middleware.env import load_env

load_env()

from frappe_cloud_deploy_middleware.frappe_cloud.deployer import main


if __name__ == "__main__":
    main()
