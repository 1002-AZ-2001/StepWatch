# Antivirus et SmartScreen

StepWatch observe les clics de la souris et capture l'écran : ce sont aussi des comportements de logiciels espions. Ajouté au format des exécutables Python (PyInstaller), cela peut déclencher des **faux positifs** d'antivirus ou l'avertissement **« Windows a protégé votre ordinateur »** de SmartScreen.

## Ce que fait StepWatch pour limiter les faux positifs

| Mesure | Effet |
| --- | --- |
| **Installateur** `StepWatch-VERSION-windows-x64-setup.exe` (recommandé) | Installation classique dans le profil, sans extraction dans un dossier temporaire à chaque lancement, comportement que les antivirus surveillent. |
| **Chargeur PyInstaller recompilé** par la CI | Le chargeur précompilé est partagé par d'innombrables programmes, malveillants compris ; StepWatch a le sien. |
| **Pas de compression UPX** | Les exécutables compressés par UPX sont souvent bloqués par principe. |
| **Informations de version et icône** | Éditeur, produit, version et description visibles dans les propriétés du fichier. |
| **Signature de code** (SignPath Foundation, dès acceptation du projet) | La signature relie le fichier au dépôt public et construit une réputation SmartScreen d'une version à l'autre. Voir la [politique de signature](../CODE_SIGNING_POLICY.md). |
| **Aucun accès réseau caché** | Seules la recherche de mises à jour (désactivable) et la synchronisation que vous configurez utilisent le réseau. |

Aucune de ces mesures ne garantit l'absence d'alerte : Microsoft indique que la réputation SmartScreen se construit par la signature et l'usage, fichier par fichier ; même un certificat EV ne la donne plus d'emblée depuis 2024.

## Vérifier un fichier avant de l'ouvrir

1. Comparez sa somme SHA-256 à celle de `SHA256SUMS-windows.txt`, publiée avec la version :

   ```powershell
   Get-FileHash .\StepWatch-VERSION-windows-x64-setup.exe -Algorithm SHA256
   ```

2. Si le fichier est signé : Propriétés › Signatures numériques › « SignPath Foundation ».

## En cas de détection

- **SmartScreen** : « Informations complémentaires » › « Exécuter quand même », seulement si la somme SHA-256 correspond.
- **Microsoft Defender** : signalez le faux positif sur le [portail d'analyse de Microsoft](https://www.microsoft.com/wdsi/filesubmission) (« Software developer » ou « Home customer »), en joignant le fichier et le lien de la version.
- **Autre antivirus** : chaque éditeur propose un formulaire de faux positif ; indiquez le lien vers la version GitHub.
- Ouvrez un [ticket](https://github.com/1002-AZ-2001/StepWatch/issues) avec le nom de l'antivirus et le nom de la détection : le projet peut alors signaler la version concernée.

N'ajoutez une exclusion dans l'antivirus qu'en dernier recours, et seulement pour un fichier dont la somme SHA-256 est vérifiée.
