#!/usr/bin/python

import json
import requests
import shutil
import xml.etree.ElementTree as ET 
import argparse
import os

class MavenProperties:
    def __init__( self ):
        self._properties = {}
    def __getitem__( self, name ):
        if name in self._properties:
            return self._properties[name]
        else:
            return ""

    def __setitem__( self, name, value ):
        self._properties[ name ] = value

    def __contains__( self, name ):
        return name in self._properties


class MavenPom:
    def __init__( self, base, groupId, artifactId, version, properties = None ):
        self._base = base
        if properties is None:
            self._properties = MavenProperties()
            self._properties[ 'project.version' ] = version
        else:
            self._properties = properties
        pom = self._download_pom( groupId, artifactId, version )
        if pom:
            self._root = ET.fromstring(pom)
            self._xmlns = self._extract_xmlns( self._root.tag )
            self._extract_properties()
            parent = self._extract_parent()
            if parent:
                self._parent = MavenPom( self._base, parent['groupId'], parent['artifactId'], parent['version'], self._properties )
            else:
                self._parent = None
            self._extract_dependency_management()
            self._extract_all_dependencies()
            self._extract_dependency_exclusion()

    def __nonzero__( self ):
        return hasattr( self, '_root' )

    def __bool__( self ):
        return hasattr( self, '_root' )

    def get_all_dependencies( self ):
        dependencies = []
        dependencies.extend( self._dependencies )
        parent = self._parent
        while parent:
            dependencies.extend( parent._dependencies )
            parent = parent._parent
        result = []
        for dep in dependencies:
            dep['groupId'] = self._eval_with_properties( dep['groupId'] )
            dep['artifactId'] = self._eval_with_properties( dep['artifactId'] )
            if 'version' not in dep:
                version = self._get_version_from_dependency_management( dep['groupId'], dep['artifactId']  )
            else:
                version = self._eval_with_properties( dep['version'] )
            if version:
                result.append( {'groupId': self._eval_with_properties( dep['groupId'] ), 
                        'artifactId': self._eval_with_properties( dep['artifactId'] ), 
                        'version': version })
        return result 

    def _get_version_from_dependency_management( self, groupId, artifactId ):
        all_dep_management = []
        all_dep_management.extend( self._dependency_management )
        parent = self._parent
        while parent:
            all_dep_management.extend( parent._dependency_management )
            parent = parent._parent
        for dep in all_dep_management:
            if 'version' in dep and groupId == self._eval_with_properties( dep['groupId'] ) and artifactId == self._eval_with_properties( dep['artifactId'] ):
                return self._eval_with_properties( dep['version'] )
        return ""

    def _extract_xmlns( self, element_name ):
        if element_name.startswith( '{' ):
            return element_name[0: element_name.find( '}' ) + 1]
        else:
            return element_name

    def _download_pom( self, groupId, artifactId, version ):
        x = groupId.split( "." )
        x.extend( [artifactId, version, "%s-%s.pom" % (artifactId, version ) ] )
        url = self._base + '/' + "/".join( x )
        print "Download pom file %s" % url 
        r = requests.get( url )
        if r.status_code >= 200 and r.status_code < 300:
            return r.content
        print "fail to get the pom file %s" % url
        return ""

    def _extract_all_dependencies( self ):
        self._dependencies = []
        deps_elem = self._root.find( '%sdependencies' % self._xmlns )
        if deps_elem is not None:
            for dep_elem in deps_elem.iter( "%sdependency" % self._xmlns ):
                groupId = dep_elem.find( "%sgroupId" % self._xmlns )
                artifactId = dep_elem.find( '%sartifactId' % self._xmlns )
                scope = dep_elem.find( '%sscope' % self._xmlns )
                version = dep_elem.find( '%sversion' % self._xmlns )
                dep = self._create_dependency( dep_elem )
                if dep:
                    self._dependencies.append( dep )

    def _extract_dependency_management( self ):
        self._dependency_management = []
        dpm = self._root.find( '%sdependencyManagement' % self._xmlns )
        if dpm is not None:
            for dep_elem in dpm.iter( "%sdependency" % self._xmlns ):
                groupId = dep_elem.find( "%sgroupId" % self._xmlns )
                artifactId = dep_elem.find( '%sartifactId' % self._xmlns )
                scope = dep_elem.find( '%sscope' % self._xmlns )
                version = dep_elem.find( '%sversion' % self._xmlns )
                dep = self._create_dependency( dep_elem )
                if dep:
                    self._dependency_management.append( dep )

    def _extract_dependency_exclusion( self ):
        deps_elem = self._root.find( '%sdependencies' % self._xmlns )
        if deps_elem is not None:
            exclusions_elem = deps_elem.iter( '%sexclusion' % self._xmlns )
            if exclusions_elem is not None:
                for exclusion_elem in exclusions_elem:
                    print ("exclusion:%r" % exclusion_elem )
    def _create_dependency( self, dep_elem ):
        groupId = dep_elem.find( "%sgroupId" % self._xmlns )
        artifactId = dep_elem.find( '%sartifactId' % self._xmlns )
        scope = dep_elem.find( '%sscope' % self._xmlns )
        version = dep_elem.find( '%sversion' % self._xmlns )
        optional = dep_elem.find( '%soptional' % self._xmlns )
        if optional is not None and optional.text.lower() in ( "t", "true", "y", "yes", "1" ):
            return None
        if scope is not None and ( scope.text.lower() == "compile" or scope.text.lower() == "test" ):
            return None
        if groupId is not None and artifactId is not None:
            dep = {'groupId': groupId.text, 'artifactId': artifactId.text}
            if scope is not None:
                dep['scope'] = scope.text
            if version is not None:
                dep['version'] = version.text
            return dep
        return None

    def _extract_parent( self ):
        parent_elem = self._root.find( '%sparent' % self._xmlns )
        if parent_elem is not None:
            groupId = parent_elem.find( '%sgroupId' % self._xmlns )
            artifactId = parent_elem.find( '%sartifactId' % self._xmlns )
            version = parent_elem.find( '%sversion' % self._xmlns )
            if groupId is not None and artifactId is not None and version is not None:
                return {'groupId': groupId.text, 'artifactId': artifactId.text, 'version': version.text }
        return None

    def _extract_properties( self ):
        properties_elem = self._root.find( '%sproperties' % self._xmlns )
        if properties_elem is None:
            return
        for prop_elem in properties_elem:
            name = self._extract_element_name_without_namespace( prop_elem.tag )
            if prop_elem.text is not None:
                self._properties[name] = prop_elem.text

    def _extract_element_name_without_namespace( self, element_tag ):
        if element_tag.startswith( '{' ):
            return element_tag[ element_tag.find( '}' ) + 1:]
        else:
            return element_tag


    def _eval_with_properties( self, val ):
        if val.startswith( '${' ) and val.endswith( '}' ):
            prop_name = val[2:-1]
            if prop_name in self._properties:
                return self._eval_with_properties( self._properties[prop_name] )
            else:
                return ""
        else:
            return val

class MavenLibraryDownloader:
    def __init__( self, base, out_dir = "." ):
        if base.endswith( '/' ):
            self._base = base[0:-1]
        else:
            self._base = base

        self._out_dir = out_dir
        self._downloaded_libraries = []

    def download( self, group, artifact, version ):
        pom = MavenPom( self._base, group, artifact, version )
        if pom:
            deps = pom.get_all_dependencies()
            self._do_download( group, artifact, version )
            self._downloaded_libraries.append( {'groupId': group, 'artifactId':artifact, 'version': version } )
            for dep in deps:
                if dep not in self._downloaded_libraries:
                    self.download( dep['groupId'], dep['artifactId'], dep['version'] )

    def _do_download( self, group, artifact, version ):
        file_name = '%s-%s.jar' % ( artifact, version )
        x = group.split( '.' )
        x.extend( [artifact,version, file_name ] )
        url = self._base + "/" + "/".join( x )
        r = requests.get(url, stream=True)
        print "download %s from %s and save to %s/%s" % ( file_name, url, self._out_dir, file_name )
        with open(self._out_dir + '/' + file_name, 'wb') as f:
            shutil.copyfileobj( r.raw, f )

        
def parse_arg():
    parser = argparse.ArgumentParser()
    parser.add_argument( '--maven_url', required=False, help='the maven http/https url', default='http://central.maven.org/maven2' )
    parser.add_argument( '--library', required = True, help = 'the library in format: groupId:artifactId:version')
    parser.add_argument( '--output', '-o', required=False, help = 'the library output directory', default=".")
    return parser.parse_args()

def main():
    args = parse_arg()
    out_dir = args.output
    maven_url = args.maven_url

    if os.path.exists( out_dir ) and not os.path.isdir( out_dir ):
        print("the output %s is not a directory" % out_dir )
        return
    if not os.path.exists( out_dir ):
        os.makedirs( out_dir )

    try:
        (group, artifact, version ) = args.library.split( ':' )
    except:
        print "library must be format: groupId:artifactId:version"
    else:
        downloader = MavenLibraryDownloader( maven_url, out_dir )
        downloader.download(group, artifact, version)


if __name__ == "__main__":
    main()
