#!/usr/bin/env python3
"""
Build locale/*/LC_MESSAGES/django.po and django.mo without GNU gettext (uses polib).

Run from project root: python scripts/build_i18n.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import polib  # type: ignore
except ImportError:
    print("Install polib: pip install polib", file=sys.stderr)
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent

# msgid (English / source) -> French translation
FR = {
    # base
    "Smart Loan Eligibility Checker": "Vérificateur d'éligibilité crédit intelligent",
    "Home": "Accueil",
    "Dashboard": "Tableau de bord",
    "Language": "Langue",
    "Switch to light mode": "Passer en mode clair",
    "Switch to dark mode": "Passer en mode sombre",
    "Log out": "Déconnexion",
    "Log in": "Connexion",
    "Sign up": "S'inscrire",
    "Business AI Team": "Équipe Business AI",
    # home
    "API-first · AI-powered": "API-first · propulsé par l'IA",
    "LoanWise guides applicants with a multilingual AI assistant, secure document analysis, and clear ROI insights — built for modern fintech teams.": (
        "LoanWise accompagne les candidats avec un assistant IA multilingue, une analyse documentaire sécurisée "
        "et une vision ROI claire — pensé pour les équipes fintech modernes."
    ),
    "Open dashboard": "Ouvrir le tableau de bord",
    "Get started": "Commencer",
    "Explore API": "Explorer l'API",
    "Documents": "Documents",
    "SHA-256 + auto-delete": "SHA-256 + suppression auto",
    "AI stack": "Stack IA",
    "LangGraph · Florence-2": "LangGraph · Florence-2",
    "Languages": "Langues",
    "FR · EN": "FR · EN",
    "Eligibility preview": "Aperçu d'éligibilité",
    "Live": "En direct",
    "Score": "Score",
    "ROI": "ROI",
    # dashboard
    "Your applications": "Vos demandes",
    "Track eligibility, documents, and AI-assisted progress.": "Suivez l'éligibilité, les documents et la progression assistée par l'IA.",
    "New application": "Nouvelle demande",
    "Creating…": "Création…",
    "No applications yet. Start your first check.": "Aucune demande pour l'instant. Lancez votre première vérification.",
    "Could not create application. Ensure you are logged in.": "Impossible de créer la demande. Vérifiez que vous êtes connecté.",
    # application detail
    "Application workspace": "Espace demande",
    "Download PDF": "Télécharger le PDF",
    "Run AI pipeline": "Lancer le pipeline IA",
    "Processing…": "Traitement…",
    "Pipeline progress": "Progression du pipeline",
    "AI assistant": "Assistant IA",
    "Step-by-step guidance in your language.": "Accompagnement pas à pas dans votre langue.",
    "Type your message…": "Votre message…",
    "Send": "Envoyer",
    "Files are hashed (SHA-256) and removed after analysis.": "Fichiers hachés (SHA-256) puis supprimés après analyse.",
    "Requirement": "Exigence",
    "generic": "générique",
    "face_selfie": "selfie visage",
    "identity": "identité",
    "Upload": "Téléverser",
    "Uploading…": "Envoi…",
    "ROI & impact": "ROI & impact",
    "Principal": "Capital",
    "Figures are indicative for the hackathon demo.": "Chiffres indicatifs pour la démo hackathon.",
    "Complete the chat steps, upload documents, then run the AI pipeline.": "Terminez le chat, téléversez les documents, puis lancez le pipeline IA.",
    "Completed": "Terminé",
    "Please verify your email before running the pipeline.": "Vérifiez votre e-mail avant de lancer le pipeline.",
    # login / register
    "Welcome back": "Bon retour",
    "Sign in to continue your loan journey.": "Connectez-vous pour poursuivre votre parcours.",
    "No account?": "Pas encore de compte ?",
    "Create your account": "Créer votre compte",
    "We will send a six-digit verification code to your email.": (
        "Nous enverrons un code à six chiffres à votre adresse e-mail."
    ),
    "Email": "E-mail",
    "Username": "Nom d'utilisateur",
    "First name": "Prénom",
    "Last name": "Nom",
    "Password": "Mot de passe",
    "Preferred language": "Langue préférée",
    "Already have an account?": "Déjà un compte ?",
    # models / admin (choices)
    "French": "Français",
    "English": "Anglais",
    "System": "Système",
    "Light": "Clair",
    "Dark": "Sombre",
    "email address": "adresse e-mail",
    "user": "utilisateur",
    "users": "utilisateurs",
    "Personal loan": "Prêt personnel",
    "Mortgage": "Immobilier",
    "Business loan": "Prêt professionnel",
    "Draft": "Brouillon",
    "In progress": "En cours",
    "Under review": "En analyse",
    "Approved": "Approuvé",
    "Rejected": "Refusé",
    "Completed": "Terminé",
    "Language for this application (UI + AI agents).": "Langue pour cette demande (interface + agents IA).",
    'List of loan type codes, e.g. ["personal", "business"].': 'Liste de codes de type de prêt, ex. ["personal", "business"].',
    "document requirement": "exigence documentaire",
    "document requirements": "exigences documentaires",
    "Generic": "Générique",
    "Identity": "Identité",
    "Income proof": "Justificatif de revenus",
    "Face selfie": "Selfie visage",
    "User": "Utilisateur",
    "Assistant": "Assistant",
    # web_views messages
    "Account created. Check your email for your verification code.": (
        "Compte créé. Consultez votre e-mail pour obtenir le code de vérification."
    ),
    "We could not send the email. Enter the code from the server log or ask an administrator.": (
        "Impossible d'envoyer l'e-mail. Saisissez le code affiché dans les journaux du serveur ou demandez à un administrateur."
    ),
    "Development: the email is printed in the runserver terminal. Your verification code is %(code)s. "
    "You can also open: %(url)s": (
        "Développement : l'e-mail est affiché dans le terminal runserver. Votre code de vérification : %(code)s. "
        "Vous pouvez aussi ouvrir : %(url)s"
    ),
    "Verify your email": "Vérifier votre e-mail",
    "Enter the six-digit code we sent to your inbox after registration.": (
        "Saisissez le code à six chiffres envoyé dans votre boîte après l'inscription."
    ),
    "Verification code": "Code de vérification",
    "Verify": "Vérifier",
    "Back to log in": "Retour à la connexion",
    "Enter verification code": "Saisir le code de vérification",
    "Please enter your email and the verification code.": (
        "Veuillez saisir votre e-mail et le code de vérification."
    ),
    "The code must contain exactly six digits.": "Le code doit comporter exactement six chiffres.",
    "Invalid email or verification code. Check the code in your email and try again.": (
        "E-mail ou code de vérification invalide. Vérifiez le code reçu par e-mail et réessayez."
    ),
    "This email was already verified. You are signed in.": (
        "Cette adresse e-mail était déjà vérifiée. Vous êtes connecté."
    ),
    "Your email is verified. Welcome!": "Votre e-mail est vérifié. Bienvenue !",
    "Please correct the errors below.": "Veuillez corriger les erreurs ci-dessous.",
    "Invalid email or password.": "E-mail ou mot de passe invalide.",
    "Application not found.": "Demande introuvable.",
    # serializers
    "Amount must be positive.": "Le montant doit être positif.",
    # admin
    "Personal info": "Informations personnelles",
    "Preferences": "Préférences",
    "Permissions": "Permissions",
    "Important dates": "Dates importantes",
    # application_detail / registration (2026)
    "Account created. You can log in — your email is marked verified in development.": (
        "Compte créé. Vous pouvez vous connecter — votre e-mail est marqué vérifié en mode développement."
    ),
    "Email verification is required to run the AI pipeline. If you did not receive an email, check the terminal where runserver is running (console email backend), or ask an administrator to mark your account as verified.": (
        "Une e-mail vérifiée est requise pour lancer le pipeline IA. Si vous n'avez rien reçu, regardez le terminal "
        "où tourne runserver (backend e-mail console), ou demandez à un administrateur de valider votre compte."
    ),
    "Development: verified email is not required to run the pipeline. Configure SMTP and set LOANWISE_REQUIRE_EMAIL_VERIFICATION=true for production-like behaviour.": (
        "Développement : l'e-mail vérifié n'est pas exigé pour le pipeline. Configurez un SMTP et "
        "LOANWISE_REQUIRE_EMAIL_VERIFICATION=true pour un comportement proche de la production."
    ),
    "Liveness video": "Vidéo de vivacité",
    "Click Start: the AI guides you in real time. When the sequence is validated, recording stops automatically and the video is sent. Compare it with your ID by running the AI pipeline.": (
        "Cliquez sur Démarrer : l’IA vous guide en temps réel. Quand la séquence est validée, "
        "l’enregistrement s’arrête et la vidéo est envoyée automatiquement. Lancez le pipeline IA pour la comparer à votre pièce d’identité."
    ),
    "Start liveness check": "Démarrer la vérification vivacité",
    "In progress…": "En cours…",
    "Sending video…": "Envoi de la vidéo…",
    "Cancel": "Annuler",
    "Video received. Run the AI pipeline to compare with your ID.": (
        "Vidéo reçue. Lancez le pipeline IA pour la comparer à votre pièce d’identité."
    ),
    "No video data to upload.": "Aucune donnée vidéo à envoyer.",
    "Open the camera and record a short clip. Slowly turn your head left, then right (about 10–20 seconds). This is compared to your ID photo when you run the pipeline.": (
        "Ouvrez la caméra et enregistrez une courte séquence. Tournez lentement la tête à gauche puis à droite "
        "(environ 10 à 20 secondes). La vidéo est comparée à la photo d’identité lors du lancement du pipeline."
    ),
    "Start camera & record": "Démarrer la caméra et enregistrer",
    "Recording…": "Enregistrement…",
    "Stop & upload video": "Arrêter et envoyer la vidéo",
    "Uploading video…": "Envoi de la vidéo…",
    "Camera recording is not supported in this browser.": "L’enregistrement caméra n’est pas pris en charge par ce navigateur.",
    "Could not access the camera.": "Impossible d’accéder à la caméra.",
    "Video upload failed.": "Échec de l’envoi de la vidéo.",
    "liveness_video": "liveness_video",
    "AI analysis output (development only)": "Sortie analyse IA (mode développement uniquement)",
    "Full JSON from face/liveness checks and document analysis — visible when DEBUG is true.": (
        "JSON complet des vérifications visage / vivacité et des analyses documentaires — visible si DEBUG est activé."
    ),
    "No face detected — position yourself in front of the camera.": (
        "Aucun visage détecté — placez-vous face à la caméra."
    ),
    "Look straight at the camera. Next, we will ask you to turn your head left, then right, then blink.": (
        "Regardez droit dans la caméra. Ensuite, nous vous demanderons de tourner la tête à gauche, puis à droite, puis de cligner des yeux."
    ),
    "Turn your head slowly to the left (your left).": "Tournez lentement la tête à gauche (votre gauche).",
    "Now turn your head slowly to the right (your right).": "Maintenant tournez lentement la tête à droite (votre droite).",
    "Blink your eyes naturally once or twice.": "Clignez des yeux une ou deux fois, naturellement.",
    "Liveness sequence complete — you can stop recording and upload.": (
        "Séquence de vivacité terminée — vous pouvez arrêter l’enregistrement et envoyer la vidéo."
    ),
    "Real-time face guidance unavailable (MediaPipe could not load). You can still record.": (
        "Guidage visage en temps réel indisponible (MediaPipe non chargé). Vous pouvez quand même enregistrer."
    ),
    "Follow the live instructions below: face visible, turn left, turn right, then blink. Then stop and upload. The recording is compared to your ID when you run the pipeline.": (
        "Suivez les instructions en direct ci-dessous : visage visible, tourner à gauche, à droite, puis cligner des yeux. "
        "Ensuite arrêtez et envoyez. La vidéo est comparée à votre pièce d’identité lors du pipeline."
    ),
    "Sequence OK — you can stop and upload.": "Séquence OK — vous pouvez arrêter et envoyer.",
    "Sequence validated — closing the camera and sending your video…": (
        "Séquence validée — fermeture de la caméra et envoi de la vidéo…"
    ),
    "Sending the video to the server…": "Envoi de la vidéo au serveur…",
    "Video received. You can run the AI pipeline to compare it with your ID.": (
        "Vidéo reçue. Vous pouvez lancer le pipeline IA pour la comparer à votre pièce d’identité."
    ),
    "Liveness cancelled.": "Vérification vivacité annulée.",
    "Real-time analysis unavailable (MediaPipe did not load). Allow scripts from the CDN or try another browser.": (
        "Analyse temps réel indisponible (MediaPipe non chargé). Autorisez les scripts du CDN ou essayez un autre navigateur."
    ),
    "Running document analysis and scoring…": "Analyse des documents et calcul du score…",
    "Admin": "Admin",
    # back-office nav & customer nav
    "Back-Office": "Arrière-guichet",
    "Requests": "Demandes",
    "Conditions": "Conditions",
    "Requirements": "Exigences",
    "Agents": "Agents",
    "My requests": "Mes demandes",
    "New request": "Nouvelle demande",
    "Notifications": "Notifications",
    # eligibility conditions page
    "Eligibility criteria": "Critères d'éligibilité",
    "Upload PDF policy documents from the bank. Text is extracted and used to guide loan eligibility for applicants.": (
        "Téléversez des documents de politique bancaire au format PDF. Le texte est "
        "extrait et sert à orienter l'éligibilité des demandeurs de prêt."
    ),
    "Add PDF document": "Ajouter un document PDF",
    "PDF file": "Fichier PDF",
    "Choose file": "Choisir un fichier",
    "No file chosen": "Aucun fichier sélectionné",
    "Uploaded criteria": "Critères téléversés",
    "No eligibility PDFs yet. Upload a bank policy document above.": (
        "Aucun PDF de critères pour l'instant. Téléversez un document de politique "
        "bancaire ci-dessus."
    ),
    "File": "Fichier",
    "Indexed": "Indexé",
    "Actions": "Actions",
    "Open file": "Ouvrir le fichier",
    "Delete": "Supprimer",
    "Delete this document?": "Supprimer ce document ?",
    "Please choose a PDF file to upload.": "Veuillez choisir un fichier PDF à téléverser.",
    "Only PDF files are accepted.": "Seuls les fichiers PDF sont acceptés.",
    "Criteria document added and indexed.": "Document de critères ajouté et indexé.",
    "Criteria document deleted.": "Document de critères supprimé.",
    "Staff: use the administration panel to manage users, loan applications, and API tokens.": (
        "Staff : utilisez l'espace d'administration pour gérer les utilisateurs, les demandes de prêt et les jetons API."
    ),
    "Open admin": "Ouvrir l'admin",
    "Administration": "Administration",
    "LoanWise — users, applications, tokens": "LoanWise — utilisateurs, demandes, jetons",
}


def write_catalog(lang: str, fr_map: dict[str, str]) -> None:
    out_dir = BASE / "locale" / lang / "LC_MESSAGES"
    out_dir.mkdir(parents=True, exist_ok=True)
    po_path = out_dir / "django.po"
    mo_path = out_dir / "django.mo"
    if po_path.exists():
        po = polib.pofile(str(po_path))
    else:
        po = polib.POFile()
        po.metadata = {
            "Project-Id-Version": "LoanWise 1.0",
            "Report-Msgid-Bugs-To": "",
            "POT-Creation-Date": "",
            "PO-Revision-Date": "",
            "Last-Translator": "",
            "Language-Team": "",
            "Language": "fr" if lang == "fr" else "en",
            "MIME-Version": "1.0",
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Transfer-Encoding": "8bit",
        }
    for msgid, fr_text in fr_map.items():
        msgstr = fr_text if lang == "fr" else msgid
        entry = po.find(msgid)
        if entry:
            entry.msgstr = msgstr
        else:
            po.append(polib.POEntry(msgid=msgid, msgstr=msgstr))
    po.save(str(po_path))
    po.save_as_mofile(str(mo_path))
    print(f"Wrote {po_path} and {mo_path}")


def main() -> None:
    write_catalog("fr", FR)
    # English catalog: msgstr == msgid (explicit .mo for Django)
    write_catalog("en", FR)


if __name__ == "__main__":
    main()
