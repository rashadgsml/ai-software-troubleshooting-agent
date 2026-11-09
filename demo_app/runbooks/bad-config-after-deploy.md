# Bad configuration after deploy
Symptoms: errors begin right after a release, name resolution failure, connection refused to a dependency, wrong URL in config.
Steps: 1) compare config changes in the latest deployment; 2) verify dependency URLs and hostnames; 3) roll back the config change or redeploy the previous version; 4) add config validation to the deploy pipeline.
