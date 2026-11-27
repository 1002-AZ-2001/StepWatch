# Politique de signature du code

Les exécutables Windows de StepWatch sont destinés à être signés gratuitement par la [SignPath Foundation](https://signpath.org), qui soutient les projets open source. La mention ci-dessous s'appliquera dès que la demande du projet aura été acceptée :

> Signature de code gratuite fournie par [SignPath.io](https://about.signpath.io), certificat de la [SignPath Foundation](https://signpath.org).

L'éditeur affiché par Windows est alors « SignPath Foundation ». Tant que la signature n'est pas active, les fichiers publiés ne sont pas signés : vérifiez leur somme SHA-256 (voir [Antivirus et SmartScreen](docs/antivirus.md)).

## Ce qui est signé

- `StepWatch.exe` du dossier portable et de l'installateur ;
- `StepWatch-VERSION-windows-x64.exe` (fichier unique) ;
- `StepWatch-VERSION-windows-x64-setup.exe` (installateur).

Seuls des fichiers construits par le workflow GitHub Actions public du dépôt ([build.yml](.github/workflows/build.yml)), sur des machines hébergées par GitHub et à partir de la branche `main` ou d'une étiquette de version, sont envoyés à la signature. Aucun fichier construit sur un poste personnel n'est signé.

## Rôles

| Rôle | Personne |
| --- | --- |
| Auteur et responsable des versions (committer, reviewer, approver) | [@1002-AZ-2001](https://github.com/1002-AZ-2001) |

Chaque demande de signature d'une version publiée est approuvée manuellement dans SignPath. Les comptes GitHub et SignPath du projet utilisent l'authentification à deux facteurs.

## Vie privée

StepWatch ne transmet aucune donnée sans action ou configuration de l'utilisateur :

- **recherche des mises à jour** : une requête à l'API publique des versions de GitHub, au plus une fois par jour, désactivable (Paramètres › Général) ;
- **synchronisation** : envoi des guides vers le serveur que l'utilisateur a lui-même configuré (Nextcloud/WebDAV, dossier réseau ou SFTP), désactivée par défaut ;
- **liens d'aide** : ouverts dans le navigateur seulement sur clic.

Aucune télémétrie, aucun compte, aucune publicité. Captures, journaux et rapports restent sur le poste, sauf synchronisation configurée.
