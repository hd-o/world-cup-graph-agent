resource "google_service_account" "database" {
  account_id   = "${var.deployment}-database"
  display_name = "Deployment database provisioning identity"
}

resource "google_secret_manager_secret" "database" {
  for_each  = toset(["bootstrap", "runtime"])
  secret_id = "${var.deployment}-database-${each.key}"
  labels    = local.labels
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "bootstrap" {
  secret_id = google_secret_manager_secret.database["bootstrap"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.database.email}"
}

resource "google_compute_disk" "database" {
  name   = "${var.deployment}-database"
  zone   = "${var.region}-a"
  size   = 30
  type   = "pd-balanced"
  labels = local.labels
}

resource "google_compute_instance" "database" {
  name         = "${var.deployment}-neo4j"
  zone         = "${var.region}-a"
  machine_type = var.database_machine
  labels       = local.labels
  tags         = ["${var.deployment}-database"]
  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 16
    }
  }
  attached_disk {
    source      = google_compute_disk.database.id
    device_name = "worldcup-data"
  }
  network_interface {
    subnetwork = google_compute_subnetwork.database.id
  }
  service_account {
    email  = google_service_account.database.email
    scopes = ["cloud-platform"]
  }
  metadata                = { enable-oslogin = "TRUE", block-project-ssh-keys = "TRUE" }
  metadata_startup_script = <<-SCRIPT
    #!/bin/bash
    set -eu
    apt-get update
    apt-get install -y docker.io
    systemctl enable --now docker
    mkdir -p /srv/worldcup
  SCRIPT
  depends_on              = [google_compute_router_nat.database]
}
