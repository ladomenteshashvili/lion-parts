"""gunicorn for the isolated staging. A starting point for a small shared host: 2 x 4 = 8 threads.
Size the production values from a staging load test (docs/operations.md section 4), not from this file."""

bind = "127.0.0.1:8011"
worker_class = "gthread"
workers = 2
threads = 4
timeout = 60
graceful_timeout = 30
max_requests = 2000
max_requests_jitter = 200
errorlog = "-"
accesslog = None  # nginx writes the masked log; a gunicorn access log would hold public link tokens
control_socket_disable = True
