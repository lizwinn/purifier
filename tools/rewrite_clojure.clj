;; SPDX-License-Identifier: MIT
;; Rewrite dependency coordinates only in the sandbox's source copy. Native
;; tools.build/create-basis calls read these files independently of CLI aliases.
(require '[clojure.edn :as edn]
         '[clojure.walk :as walk]
         '[clojure.java.io :as io])

(let [[configuration root] *command-line-args*
      overrides (get-in (edn/read-string (slurp configuration))
                        [:aliases :nix-purifier-project-inputs :override-deps])
      rewrite (fn [form]
                (if (map? form)
                  (into (empty form)
                        (map (fn [[library coordinate]]
                               [library (if (and (symbol? library)
                                                 (map? coordinate)
                                                 (:mvn/version coordinate)
                                                 (contains? overrides library))
                                          (assoc coordinate :mvn/version
                                                 (:mvn/version (get overrides library)))
                                          coordinate)]))
                        form)
                  form))]
  (doseq [file (file-seq (io/file root))
          :when (and (.isFile file)
                     (= "deps.edn" (.getName file))
                     (not (some #{"target" ".git" ".cpcache" ".clojure-cli-purifier"}
                                (seq (.split (.getPath file) "/")))))]
    (let [original (edn/read-string (slurp file))
          selected (walk/postwalk rewrite original)]
      (when (not= original selected)
        (spit file (str (pr-str selected) "\n"))))))
