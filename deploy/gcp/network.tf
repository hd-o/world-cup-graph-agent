resource "google_compute_network" "application" {
  name                                      = var.deployment
  auto_create_subnetworks                   = false
  network_firewall_policy_enforcement_order = "BEFORE_CLASSIC_FIREWALL"
}

resource "google_compute_subnetwork" "agent" {
  name                     = "${var.deployment}-agent"
  region                   = var.region
  network                  = google_compute_network.application.id
  ip_cidr_range            = "10.43.1.0/26"
  stack_type               = "IPV4_ONLY"
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "database" {
  name                     = "${var.deployment}-database"
  region                   = var.region
  network                  = google_compute_network.application.id
  ip_cidr_range            = "10.43.2.0/24"
  stack_type               = "IPV4_ONLY"
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "gateway" {
  name                     = "${var.deployment}-gateway"
  region                   = var.region
  network                  = google_compute_network.application.id
  ip_cidr_range            = "10.43.3.0/26"
  stack_type               = "IPV4_ONLY"
  private_ip_google_access = true
}

resource "google_compute_firewall" "bolt" {
  name          = "${var.deployment}-bolt"
  network       = google_compute_network.application.id
  source_ranges = [google_compute_subnetwork.agent.ip_cidr_range]
  target_tags   = ["${var.deployment}-database"]
  allow {
    protocol = "tcp"
    ports    = ["7687"]
  }
}

resource "google_compute_firewall" "operator" {
  name          = "${var.deployment}-iap"
  network       = google_compute_network.application.id
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["${var.deployment}-database"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_network_firewall_policy" "agent" {
  name = "${var.deployment}-agent"
}

resource "google_compute_network_firewall_policy_association" "agent" {
  name              = "${var.deployment}-agent"
  firewall_policy   = google_compute_network_firewall_policy.agent.name
  attachment_target = google_compute_network.application.id
}

resource "google_compute_network_firewall_policy_rule" "database" {
  firewall_policy = google_compute_network_firewall_policy.agent.name
  priority        = 100
  direction       = "EGRESS"
  action          = "allow"
  match {
    src_ip_ranges  = [google_compute_subnetwork.agent.ip_cidr_range]
    dest_ip_ranges = ["${google_compute_instance.database.network_interface[0].network_ip}/32"]
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["7687"]
    }
  }
}

resource "google_compute_network_firewall_policy_rule" "google" {
  firewall_policy = google_compute_network_firewall_policy.agent.name
  priority        = 200
  direction       = "EGRESS"
  action          = "allow"
  match {
    src_ip_ranges  = [google_compute_subnetwork.agent.ip_cidr_range]
    dest_ip_ranges = ["199.36.153.8/30"]
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["443"]
    }
  }
}

resource "google_compute_network_firewall_policy_rule" "openrouter" {
  firewall_policy = google_compute_network_firewall_policy.agent.name
  priority        = 300
  direction       = "EGRESS"
  action          = "allow"
  match {
    src_ip_ranges = [google_compute_subnetwork.agent.ip_cidr_range]
    dest_fqdns    = ["openrouter.ai"]
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["443"]
    }
  }
}

resource "google_compute_network_firewall_policy_rule" "denial" {
  firewall_policy = google_compute_network_firewall_policy.agent.name
  priority        = 1000
  direction       = "EGRESS"
  action          = "deny"
  match {
    src_ip_ranges  = [google_compute_subnetwork.agent.ip_cidr_range]
    dest_ip_ranges = ["0.0.0.0/0"]
    layer4_configs { ip_protocol = "all" }
  }
}

# Only the database and agent need public IPv4 translation. Gateway calls to
# Google APIs and the internal agent use Private Google Access instead.
resource "google_compute_router" "database" {
  name    = "${var.deployment}-database"
  region  = var.region
  network = google_compute_network.application.id
}

resource "google_compute_router_nat" "database" {
  name                               = "${var.deployment}-database"
  router                             = google_compute_router.database.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.database.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
  subnetwork {
    name                    = google_compute_subnetwork.agent.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

resource "google_dns_managed_zone" "private" {
  name       = "${var.deployment}-private"
  dns_name   = "worldcup.internal."
  visibility = "private"
  private_visibility_config {
    networks { network_url = google_compute_network.application.id }
  }
}

resource "google_dns_record_set" "database" {
  managed_zone = google_dns_managed_zone.private.name
  name         = "neo4j.worldcup.internal."
  type         = "A"
  ttl          = 60
  rrdatas      = [google_compute_instance.database.network_interface[0].network_ip]
}

resource "google_dns_managed_zone" "google" {
  for_each   = toset(["googleapis.com", "run.app"])
  name       = "${var.deployment}-${replace(each.key, ".", "-")}"
  dns_name   = "${each.key}."
  visibility = "private"
  private_visibility_config {
    networks { network_url = google_compute_network.application.id }
  }
}

resource "google_dns_record_set" "google" {
  managed_zone = google_dns_managed_zone.google["googleapis.com"].name
  name         = "private.googleapis.com."
  type         = "A"
  ttl          = 60
  rrdatas      = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
}

resource "google_dns_record_set" "apis" {
  for_each     = google_dns_managed_zone.google
  managed_zone = each.value.name
  name         = "*.${each.value.dns_name}"
  type         = "CNAME"
  ttl          = 60
  rrdatas      = [google_dns_record_set.google.name]
}
